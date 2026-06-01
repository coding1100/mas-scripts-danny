"""
Phase 2 — MAS Data Quality Audit (read-only, runs against the LOCAL clone).

Produces a plain-language report for Daniel plus detailed CSVs for the team.

Usage:
    python analysis/data_quality_audit.py                  # full dataset
    python analysis/data_quality_audit.py --store 001      # one store
    python analysis/data_quality_audit.py --start 2020-01-01 --end 2025-12-31

Notes on the data (all columns are stored as TEXT in MAS):
  - Dates are MM/DD/YY (2-digit year). For 1995-2025 the %y pivot is unambiguous.
  - Money is plain decimal text, sometimes ".00". Parsed with errors -> NaN.
  - Store/location code lives on mas_customers.location. Order-level from_location
    is ~99% empty, so an order's store is derived via customer_id -> account_id.
"""
import argparse
import os
import re
import sys

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_local_engine  # noqa: E402

EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")
os.makedirs(EXPORT_DIR, exist_ok=True)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Obvious placeholder / non-deliverable email patterns
EMAIL_JUNK = re.compile(r"(?:noemail|no-email|none@|n/?a@|test@|@test|@example|@none|xxx)", re.I)
# Placeholder customer names (house/cash/web accounts, tests)
NAME_JUNK = re.compile(r"\b(?:cash|walk\s*-?\s*in|walkin|web\s*order|internet|house\s*account|"
                       r"test|sample|do\s*not\s*use|no\s*name|unknown)\b", re.I)


def parse_money(s: pd.Series) -> pd.Series:
    cleaned = s.astype("string").str.replace(r"[,$\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def parse_mdy(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype("string").str.strip(), format="%m/%d/%y", errors="coerce")


def pct(n, d) -> str:
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


def valid_phone(s: pd.Series) -> pd.Series:
    digits = s.astype("string").str.replace(r"\D", "", regex=True)
    ten = digits.str.len() == 10
    eleven_us = (digits.str.len() == 11) & digits.str.startswith("1")
    return ten | eleven_us


def load_data(engine, store, start, end):
    cust = pd.read_sql(
        "SELECT account_id, name, address1, city, state, zip, phone1, phone2, "
        "email, class, location, last_purchase, opened_date, lifetime_sales, "
        "ytd_sales, lytd_sales FROM mas_customers",
        engine,
    )
    orders = pd.read_sql(
        "SELECT order_id, customer_id, order_date, total_amount, order_status, "
        "from_location FROM mas_orders",
        engine,
    )

    # Normalise
    cust["location"] = cust["location"].astype("string").str.strip()
    cust["last_purchase_dt"] = parse_mdy(cust["last_purchase"])
    cust["lifetime_sales_num"] = parse_money(cust["lifetime_sales"])
    orders["customer_id"] = orders["customer_id"].astype("string").str.strip()
    orders["order_date_dt"] = parse_mdy(orders["order_date"])
    orders["total_num"] = parse_money(orders["total_amount"])

    # Derive each order's store from the customer it belongs to
    loc_map = cust.set_index(cust["account_id"].astype("string").str.strip())["location"]
    orders["store"] = orders["customer_id"].map(loc_map)

    # Optional filters
    if store:
        cust = cust[cust["location"] == store]
        orders = orders[orders["store"] == store]
    if start:
        orders = orders[orders["order_date_dt"] >= pd.Timestamp(start)]
    if end:
        orders = orders[orders["order_date_dt"] <= pd.Timestamp(end)]

    return cust, orders


def audit_customers(cust):
    n = len(cust)
    name = cust["name"].astype("string").str.strip()
    email = cust["email"].astype("string").str.strip()

    email_present = email.notna() & (email != "")
    email_valid = email_present & email.str.match(EMAIL_RE) & ~email.str.contains(EMAIL_JUNK, na=False)
    phone_present = (cust["phone1"].astype("string").str.strip().fillna("") != "") | \
                    (cust["phone2"].astype("string").str.strip().fillna("") != "")
    phone_ok = valid_phone(cust["phone1"]) | valid_phone(cust["phone2"])
    addr_complete = (
        (cust["address1"].astype("string").str.strip().fillna("") != "")
        & (cust["city"].astype("string").str.strip().fillna("") != "")
        & (cust["state"].astype("string").str.strip().fillna("") != "")
        & (cust["zip"].astype("string").str.strip().fillna("") != "")
    )
    name_junk = name.fillna("").str.contains(NAME_JUNK, na=False) | (name.fillna("") == "")

    # Duplicates
    dup_email = email_valid & email.duplicated(keep=False)
    p1 = cust["phone1"].astype("string").str.replace(r"\D", "", regex=True)
    dup_phone = (p1.str.len() >= 10) & p1.duplicated(keep=False)

    metrics = {
        "Total customers": n,
        "Has email (any)": (email_present.sum(), pct(email_present.sum(), n)),
        "Has VALID, deliverable email": (email_valid.sum(), pct(email_valid.sum(), n)),
        "Has phone (any)": (phone_present.sum(), pct(phone_present.sum(), n)),
        "Has valid phone (10-digit)": (phone_ok.sum(), pct(phone_ok.sum(), n)),
        "Complete mailing address": (addr_complete.sum(), pct(addr_complete.sum(), n)),
        "Junk / placeholder name": (name_junk.sum(), pct(name_junk.sum(), n)),
        "Has a last_purchase date": (cust["last_purchase_dt"].notna().sum(),
                                     pct(cust["last_purchase_dt"].notna().sum(), n)),
        "Customers sharing a duplicate email": (dup_email.sum(), pct(dup_email.sum(), n)),
        "Customers sharing a duplicate phone": (dup_phone.sum(), pct(dup_phone.sum(), n)),
    }
    return metrics, email_valid, phone_ok


def audit_orders(cust, orders):
    n = len(orders)
    blank_cust = orders["customer_id"].fillna("") == ""
    joinable = orders["store"].notna() & (orders["store"] != "")
    status = orders["order_status"].astype("string").str.strip().str.lower()
    cancelled = status.eq("cancelled")
    metrics = {
        "Total orders": n,
        "Orders with NO customer_id (orphan)": (blank_cust.sum(), pct(blank_cust.sum(), n)),
        "Orders attributable to a store": (joinable.sum(), pct(joinable.sum(), n)),
        "Cancelled orders": (cancelled.sum(), pct(cancelled.sum(), n)),
        "Orders with parseable date": (orders["order_date_dt"].notna().sum(),
                                       pct(orders["order_date_dt"].notna().sum(), n)),
        "Orders with parseable total": (orders["total_num"].notna().sum(),
                                        pct(orders["total_num"].notna().sum(), n)),
    }
    return metrics


def store_breakdown(cust, orders):
    cg = cust.groupby("location", dropna=False).agg(
        customers=("account_id", "count"),
        lifetime_sales=("lifetime_sales_num", "sum"),
    )
    og = orders[orders["order_status"].astype("string").str.lower() != "cancelled"].groupby(
        "store", dropna=False
    ).agg(orders=("order_id", "count"), revenue=("total_num", "sum"))
    out = cg.join(og, how="outer")

    def _label(x):
        if pd.isna(x):
            return "(orphan - no customer_id)"
        s = str(x).strip()
        return s if s else "(blank location code)"

    out.index = [_label(x) for x in out.index]
    out = out.sort_values("customers", ascending=False)
    out["lifetime_sales"] = out["lifetime_sales"].round(0)
    out["revenue"] = out["revenue"].round(0)
    return out


def date_coverage(orders):
    d = orders["order_date_dt"].dropna()
    if d.empty:
        return None, None, pd.Series(dtype=int)
    by_year = d.dt.year.value_counts().sort_index()
    return d.min(), d.max(), by_year


def write_report(cust_m, order_m, stores, dmin, dmax, by_year, args, n_cust, n_ord):
    lines = []
    w = lines.append
    w("# MAS Data Quality Report")
    scope = []
    if args.store:
        scope.append(f"store {args.store}")
    if args.start or args.end:
        scope.append(f"orders {args.start or '...'} to {args.end or '...'}")
    w(f"_Scope: {', '.join(scope) if scope else 'all stores, all dates'}_\n")

    w("## Customer list quality")
    for k, v in cust_m.items():
        w(f"- **{k}:** {v[0]:,} ({v[1]})" if isinstance(v, tuple) else f"- **{k}:** {v:,}")

    w("\n## Orders")
    for k, v in order_m.items():
        w(f"- **{k}:** {v[0]:,} ({v[1]})" if isinstance(v, tuple) else f"- **{k}:** {v:,}")

    w("\n## Order history coverage")
    if dmin is not None:
        w(f"- Earliest order: **{dmin.date()}**  |  Latest order: **{dmax.date()}**")
        w(f"- Orders span **{by_year.index.min()}–{by_year.index.max()}** "
          f"across {len(by_year)} years")

    w("\n## By store")
    w("| Store | Customers | Lifetime sales | Non-cancelled orders | Order revenue |")
    w("|---|---:|---:|---:|---:|")
    for idx, r in stores.iterrows():
        c = int(r["customers"]) if pd.notna(r["customers"]) else 0
        ls = f"${r['lifetime_sales']:,.0f}" if pd.notna(r["lifetime_sales"]) else "-"
        o = f"{int(r['orders']):,}" if pd.notna(r["orders"]) else "-"
        rev = f"${r['revenue']:,.0f}" if pd.notna(r["revenue"]) else "-"
        w(f"| {idx} | {c:,} | {ls} | {o} | {rev} |")

    path = os.path.join(EXPORT_DIR, "data_quality_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", help="filter to a single location code, e.g. 001")
    ap.add_argument("--start", help="orders on/after YYYY-MM-DD")
    ap.add_argument("--end", help="orders on/before YYYY-MM-DD")
    args = ap.parse_args()

    engine = get_local_engine()
    print("Loading data from local clone...")
    cust, orders = load_data(engine, args.store, args.start, args.end)
    print(f"  customers: {len(cust):,}   orders: {len(orders):,}")

    cust_m, email_valid, phone_ok = audit_customers(cust)
    order_m = audit_orders(cust, orders)
    stores = store_breakdown(cust, orders)
    dmin, dmax, by_year = date_coverage(orders)

    # CSV exports
    stores.to_csv(os.path.join(EXPORT_DIR, "store_summary.csv"))
    by_year.rename("orders").to_csv(os.path.join(EXPORT_DIR, "orders_by_year.csv"))

    report = write_report(cust_m, order_m, stores, dmin, dmax, by_year, args, len(cust), len(orders))

    # Console summary
    print("\n=== CUSTOMER QUALITY ===")
    for k, v in cust_m.items():
        print(f"  {k:42} {v[0]:>9,}  {v[1]}" if isinstance(v, tuple) else f"  {k:42} {v:>9,}")
    print("\n=== ORDERS ===")
    for k, v in order_m.items():
        print(f"  {k:42} {v[0]:>9,}  {v[1]}" if isinstance(v, tuple) else f"  {k:42} {v:>9,}")
    if dmin is not None:
        print(f"\n  Order date range: {dmin.date()} -> {dmax.date()} ({len(by_year)} years)")
    print(f"\nReport written: {report}")
    print(f"CSVs written:   {EXPORT_DIR}\\store_summary.csv, orders_by_year.csv")


if __name__ == "__main__":
    main()
