"""
Phase 4 — RFM / VIP / Lapsed segmentation for remarketing (read-only, LOCAL clone).

Produces CRM/ad-ready CSV segments plus a plain-language summary.

Scoring inputs (all from the reliable, MAS-precomputed customer fields):
  - Recency   = days since `last_purchase`     (100% coverage)
  - Frequency = # linked non-cancelled orders   (98.6% coverage; LOWER BOUND -
                some of a customer's orders are orphaned with no customer_id)
  - Monetary  = `lifetime_sales`                (97% have spend > 0)

Usage:
    python analysis/rfm_segmentation.py
    python analysis/rfm_segmentation.py --store 001
    python analysis/rfm_segmentation.py --lapsed-months 18 --asof 2025-05-19
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_local_engine  # noqa: E402

EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")
SEG_DIR = os.path.join(EXPORT_DIR, "segments")
os.makedirs(SEG_DIR, exist_ok=True)


def parse_money(s):
    return pd.to_numeric(s.astype("string").str.replace(r"[,$\s]", "", regex=True), errors="coerce")


def parse_mdy(s):
    return pd.to_datetime(s.astype("string").str.strip(), format="%m/%d/%y", errors="coerce")


def f_score(freq):
    # Fixed, interpretable thresholds (frequency is tie-heavy + a lower bound)
    bins = [-1, 0, 1, 2, 4, 9, np.inf]
    labels = [0, 1, 2, 3, 4, 5]  # 0 = no linked orders
    return pd.cut(freq, bins=bins, labels=labels).astype(int)


def quintile(series, ascending):
    """1..5 score via rank-based quintiles. ascending=True -> small value = score 1."""
    r = series.rank(method="first", ascending=ascending)
    return pd.qcut(r, 5, labels=[1, 2, 3, 4, 5]).astype(int)


def segment(row):
    r, f = row["R"], row["F"]
    if r >= 4 and f >= 4:
        return "Champions"
    if r >= 3 and f >= 3:
        return "Loyal"
    if r >= 4 and f >= 2:
        return "Potential Loyalist"
    if r >= 4 and f <= 1:
        return "New / Recent"
    if r == 3 and f <= 2:
        return "Promising / Needs Attention"
    if r == 2 and f >= 4:
        return "Can't Lose Them"
    if r <= 2 and f >= 3:
        return "At Risk"
    if r <= 2 and f == 2:
        return "Hibernating"
    return "Lost"


# Plain-language meaning + recommended remarketing action per segment
SEG_PLAYBOOK = {
    "Champions": "Recent, frequent, high-spend. Reward & ask for referrals; early access to new collections.",
    "Loyal": "Buy regularly. Upsell premium bouquets; loyalty perks.",
    "Potential Loyalist": "Recent, a few purchases. Nudge to 3rd purchase; bundle offers.",
    "New / Recent": "Bought recently, usually once. Onboard & give a reason to return (occasion reminders).",
    "Promising / Needs Attention": "Mid recency, low frequency. Time-limited offer to re-engage.",
    "At Risk": "Were valuable, haven't bought in a long time. Win-back campaign with incentive.",
    "Can't Lose Them": "High-value frequent buyers gone quiet. Personal, high-touch win-back — priority.",
    "Hibernating": "Low recency & frequency. Low-cost reactivation (email).",
    "Lost": "Old single purchases. Cheapest channel only; expect low return.",
}


def write_report(seg_summary, scored, asof, args, vip_cut, exported):
    L = []
    w = L.append
    w("# MAS Remarketing Segments (RFM)")
    w(f"_Scope: {'store ' + args.store if args.store else 'all stores'}  |  "
      f"recency anchored at latest purchase in data ({asof.date()})  |  "
      f"{len(scored):,} customers scored_\n")
    w("**How to read this:** Recency = how recently they bought, Frequency = how many "
      "(linked) orders, Monetary = lifetime spend. Frequency is a **lower bound** "
      "(some orders in MAS have no customer link).\n")
    w("## Segments — biggest lifetime value first")
    w("| Segment | Customers | Avg lifetime $ | Total lifetime $ | What it means & what to do |")
    w("|---|---:|---:|---:|---|")
    for seg, r in seg_summary.iterrows():
        w(f"| **{seg}** | {int(r['customers']):,} | ${r['avg_lifetime']:,.0f} | "
          f"${r['total_lifetime']:,.0f} | {SEG_PLAYBOOK.get(seg, '')} |")
    w(f"\n## Money segments (for ad/CRM targeting)")
    w(f"- **VIPs** (top {100-args.vip_percentile:.0f}% by lifetime spend, ≥ ${vip_cut:,.0f}): "
      f"**{int(scored['is_vip'].sum()):,}** customers")
    w(f"- **Lapsed VIPs** (VIPs with no purchase in {args.lapsed_months}+ months) — "
      f"the highest-priority win-back list: **{exported.get('lapsed_vip_winback.csv', 0):,}** contactable")
    w(f"- **All lapsed** (no purchase in {args.lapsed_months}+ months): "
      f"**{exported.get(f'lapsed_{args.lapsed_months}mo.csv', 0):,}** contactable")
    w("\n## Files")
    w("- `exports/rfm_all_customers.csv` — every customer scored (R/F/M, segment, flags)")
    w("- `exports/segments/*.csv` — ready-to-upload lists per segment (contactable only)")
    w("\n_Note: recency is relative to the data's most recent purchase, not today, so the "
      "8-month-old migration doesn't mislabel everyone as lapsed. Re-run after a fresh "
      "migration to update._")
    path = os.path.join(EXPORT_DIR, "rfm_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def load(engine, store):
    cust = pd.read_sql(
        "SELECT account_id, name, email, phone1, location, last_purchase, lifetime_sales "
        "FROM mas_customers", engine)
    freq = pd.read_sql(
        "SELECT trim(customer_id) AS cid, count(*) AS freq FROM mas_orders "
        "WHERE coalesce(trim(customer_id),'')<>'' AND lower(order_status)<>'cancelled' "
        "GROUP BY 1", engine)

    cust["acct"] = cust["account_id"].astype("string").str.strip()
    cust["location"] = cust["location"].astype("string").str.strip()
    cust["last_purchase_dt"] = parse_mdy(cust["last_purchase"])
    cust["monetary"] = parse_money(cust["lifetime_sales"]).fillna(0).clip(lower=0)
    cust = cust.merge(freq, left_on="acct", right_on="cid", how="left")
    cust["frequency"] = cust["freq"].fillna(0).astype(int)

    if store:
        cust = cust[cust["location"] == store].copy()
    return cust


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", help="filter to one location code, e.g. 001")
    ap.add_argument("--asof", help="recency anchor YYYY-MM-DD (default = latest purchase in data)")
    ap.add_argument("--lapsed-months", type=int, default=12)
    ap.add_argument("--vip-percentile", type=float, default=95.0)
    args = ap.parse_args()

    engine = get_local_engine()
    print("Loading...")
    c = load(engine, args.store)

    asof = pd.Timestamp(args.asof) if args.asof else c["last_purchase_dt"].max()
    c["recency_days"] = (asof - c["last_purchase_dt"]).dt.days

    scored = c[c["recency_days"].notna()].copy()
    scored["R"] = quintile(scored["recency_days"], ascending=False)  # fewer days -> higher score
    scored["M"] = quintile(scored["monetary"], ascending=True)       # more $ -> higher
    scored["F"] = f_score(scored["frequency"])
    scored["RFM"] = scored["R"].astype(str) + scored["F"].astype(str) + scored["M"].astype(str)
    scored["segment"] = scored.apply(segment, axis=1)

    vip_cut = np.percentile(scored["monetary"], args.vip_percentile)
    scored["is_vip"] = scored["monetary"] >= vip_cut
    lapsed_days = args.lapsed_months * 30
    scored["is_lapsed"] = scored["recency_days"] > lapsed_days
    reachable = (scored["email"].astype("string").str.contains("@", na=False)) | \
                (scored["phone1"].astype("string").str.replace(r"\D", "", regex=True).str.len() >= 10)

    out_cols = ["acct", "name", "email", "phone1", "location", "last_purchase",
                "recency_days", "frequency", "monetary", "R", "F", "M", "RFM",
                "segment", "is_vip", "is_lapsed"]
    scored[out_cols].to_csv(os.path.join(EXPORT_DIR, "rfm_all_customers.csv"), index=False)

    # Actionable segment exports (contactable customers only)
    def export_seg(df, fname):
        df = df[reachable.reindex(df.index, fill_value=False)]
        df[out_cols].to_csv(os.path.join(SEG_DIR, fname), index=False)
        return len(df)

    exports = {
        "champions.csv": scored[scored["segment"] == "Champions"],
        "at_risk.csv": scored[scored["segment"] == "At Risk"],
        "cant_lose_them.csv": scored[scored["segment"] == "Can't Lose Them"],
        "vip_all.csv": scored[scored["is_vip"]],
        "lapsed_vip_winback.csv": scored[scored["is_vip"] & scored["is_lapsed"]],
        f"lapsed_{args.lapsed_months}mo.csv": scored[scored["is_lapsed"]],
    }
    exported = {k: export_seg(v, k) for k, v in exports.items()}

    # Summary
    seg_summary = scored.groupby("segment").agg(
        customers=("acct", "count"),
        avg_recency_days=("recency_days", "mean"),
        avg_frequency=("frequency", "mean"),
        avg_lifetime=("monetary", "mean"),
        total_lifetime=("monetary", "sum"),
    ).sort_values("total_lifetime", ascending=False).round(0)
    seg_summary.to_csv(os.path.join(EXPORT_DIR, "rfm_segment_summary.csv"))
    write_report(seg_summary, scored, asof, args, vip_cut, exported)

    print(f"\nScope: {'store ' + args.store if args.store else 'all stores'}  |  "
          f"as-of {asof.date()}  |  scored {len(scored):,} customers")
    print("\n=== SEGMENTS (by total lifetime value) ===")
    print(seg_summary.to_string())
    print("\n=== KEY EXPORTS (contactable only) ===")
    print(f"  VIPs (top {100-args.vip_percentile:.0f}%, >= ${vip_cut:,.0f} lifetime): "
          f"{scored['is_vip'].sum():,}")
    for fname, n in exported.items():
        print(f"  segments/{fname:28} {n:>7,}")
    print(f"\nFull scored table: exports/rfm_all_customers.csv")


if __name__ == "__main__":
    main()
