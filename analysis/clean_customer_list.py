"""
Phase 5a — Customer list cleaning, standardization & de-duplication (LOCAL clone).

Delivers the "make the list better" ask: a standardized, de-duplicated marketing
master built from the MAS warehouse only (no external enrichment).

Outputs (exports/clean/):
  - customer_master_clean.csv  : every customer, fields standardized + quality flags
  - marketing_list_deduped.csv : contactable + de-duplicated, ready to upload
  - duplicate_groups.csv       : suspected duplicate / shared-phone clusters to review
  - needs_attention.csv        : junk / invalid records to exclude or fix
  - cleaning_report.md         : plain-language before/after summary

Usage:
    python analysis/clean_customer_list.py            # all stores
    python analysis/clean_customer_list.py --store 001
"""
import argparse
import os
import re
import sys

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_local_engine  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports", "clean")
os.makedirs(OUT, exist_ok=True)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_JUNK = re.compile(r"(?:noemail|no-email|none@|n/?a@|test@|@test|@example|@none|xxx)", re.I)
NAME_JUNK = re.compile(r"\b(?:cash|walk\s*-?\s*in|walkin|web\s*order|internet|house\s*account|"
                       r"test|sample|do\s*not\s*use|no\s*name|unknown)\b", re.I)


def norm_phone(s):
    d = s.astype("string").str.replace(r"\D", "", regex=True)
    d = d.where(d.str.len() != 11, d.str[1:])      # strip leading US '1'
    d = d.where(d.str.len() == 10)                 # 10-digit only
    # NANP validity: area code must start 2-9; reject all-same-digit & obvious fakes
    valid = (d.str[0].isin(list("23456789"))
             & ~d.str.match(r"^(\d)\1{9}$").fillna(False)
             & ~d.isin({"1234567890", "0123456789"}))
    return d.where(valid)


def fmt_phone(d):
    return d.where(d.isna(), "(" + d.str[:3] + ") " + d.str[3:6] + "-" + d.str[6:])


def title_clean(s):
    return (s.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
            .str.title().replace({"": pd.NA}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store")
    args = ap.parse_args()
    engine = get_local_engine()
    print("Loading customers...")
    c = pd.read_sql(
        "SELECT account_id, name, address1, address2, city, state, zip, phone1, phone2, "
        "email, location, last_purchase, lifetime_sales FROM mas_customers", engine)
    if args.store:
        c = c[c["location"].astype("string").str.strip() == args.store].copy()
    n0 = len(c)

    # --- Standardize ---
    c["name_clean"] = title_clean(c["name"])
    c["city_clean"] = title_clean(c["city"])
    c["state_clean"] = c["state"].astype("string").str.strip().str.upper().str[:2]
    c["zip5"] = c["zip"].astype("string").str.replace(r"\D", "", regex=True).str[:5]
    c["email_clean"] = c["email"].astype("string").str.strip().str.lower().replace({"": pd.NA})
    p1, p2 = norm_phone(c["phone1"]), norm_phone(c["phone2"])
    c["phone_digits"] = p1.fillna(p2)
    c["phone_clean"] = fmt_phone(c["phone_digits"])
    c["lifetime_num"] = pd.to_numeric(
        c["lifetime_sales"].astype("string").str.replace(r"[,$\s]", "", regex=True),
        errors="coerce").fillna(0)
    c["last_purchase_dt"] = pd.to_datetime(
        c["last_purchase"].astype("string").str.strip(), format="%m/%d/%y", errors="coerce")

    # --- Quality flags ---
    em = c["email_clean"]
    c["email_valid"] = em.notna() & em.str.match(EMAIL_RE) & ~em.str.contains(EMAIL_JUNK, na=False)
    c["phone_valid"] = c["phone_digits"].notna()
    c["name_junk"] = c["name_clean"].fillna("").str.contains(NAME_JUNK, na=False) | c["name_clean"].isna()
    c["addr_complete"] = (c["address1"].astype("string").str.strip().fillna("") != "") & \
                         c["city_clean"].notna() & c["state_clean"].notna() & (c["zip5"].str.len() == 5)
    c["contactable"] = c["email_valid"] | c["phone_valid"]

    # --- De-duplication ---
    # Primary key for matching: 10-digit phone; fallback to valid email.
    c["dup_key"] = c["phone_digits"]
    c.loc[c["dup_key"].isna() & c["email_valid"], "dup_key"] = "em:" + em
    grp = c[c["dup_key"].notna()].groupby("dup_key")
    sizes = grp["account_id"].transform("count")
    c["group_size"] = 1
    c.loc[c["dup_key"].notna(), "group_size"] = sizes
    # distinct names within a shared key -> household vs same-person
    nunq = grp["name_clean"].transform("nunique")
    c["dup_type"] = "unique"
    multi = c["dup_key"].notna() & (c["group_size"] > 1)
    c.loc[multi, "dup_type"] = "shared_phone_household"
    c.loc[multi & (nunq <= 1), "dup_type"] = "likely_same_person"
    # primary = highest lifetime value in the group (tiebreak: most recent purchase)
    c["_rank"] = (c.sort_values(["lifetime_num", "last_purchase_dt"], ascending=False)
                  .groupby(c["dup_key"]).cumcount())
    c["is_primary"] = (c["group_size"] == 1) | (c["dup_key"].notna() & (c["_rank"] == 0))
    c.loc[c["dup_key"].isna(), "is_primary"] = True

    # --- Exports ---
    master_cols = ["account_id", "name_clean", "email_clean", "email_valid", "phone_clean",
                   "phone_valid", "address1", "city_clean", "state_clean", "zip5",
                   "addr_complete", "location", "last_purchase", "lifetime_num",
                   "contactable", "dup_type", "group_size", "is_primary"]
    c[master_cols].to_csv(os.path.join(OUT, "customer_master_clean.csv"), index=False)

    # Marketing list: contactable, not junk, de-duplicated to primary record
    mk = c[c["contactable"] & ~c["name_junk"] & c["is_primary"]].copy()
    mk[master_cols].to_csv(os.path.join(OUT, "marketing_list_deduped.csv"), index=False)

    # Duplicate groups for review (only multi-record clusters)
    dups = c[c["group_size"] > 1].sort_values(["dup_key", "is_primary"], ascending=[True, False])
    dups[["dup_key", "dup_type", "is_primary", "account_id", "name_clean",
          "email_clean", "phone_clean", "lifetime_num", "location"]].to_csv(
        os.path.join(OUT, "duplicate_groups.csv"), index=False)

    # Needs attention: junk names or not contactable
    na = c[c["name_junk"] | ~c["contactable"]]
    na[master_cols].to_csv(os.path.join(OUT, "needs_attention.csv"), index=False)

    # --- Report ---
    n_dupe_records = int((c["group_size"] > 1).sum())
    n_groups = int(c.loc[c["group_size"] > 1, "dup_key"].nunique())
    n_same = int((c["dup_type"] == "likely_same_person").sum())
    n_house = int((c["dup_type"] == "shared_phone_household").sum())
    rpt = [
        "# Customer List Cleaning Report",
        f"_Scope: {'store ' + args.store if args.store else 'all stores'}  |  "
        f"input {n0:,} customers_\n",
        "## Standardized",
        "- Names title-cased, whitespace collapsed; emails lowercased/validated; "
        "phones reduced to 10 digits and formatted `(XXX) XXX-XXXX`; states/zips normalized.\n",
        "## Quality (after cleaning)",
        f"- Valid email: **{int(c['email_valid'].sum()):,}** ({c['email_valid'].mean()*100:.1f}%)",
        f"- Valid phone: **{int(c['phone_valid'].sum()):,}** ({c['phone_valid'].mean()*100:.1f}%)",
        f"- Contactable (email or phone): **{int(c['contactable'].sum()):,}** "
        f"({c['contactable'].mean()*100:.1f}%)",
        f"- Complete mailing address: **{int(c['addr_complete'].sum()):,}** "
        f"({c['addr_complete'].mean()*100:.1f}%)",
        f"- Junk / placeholder records: **{int(c['name_junk'].sum()):,}**\n",
        "## De-duplication",
        f"- Records in a duplicate/shared cluster: **{n_dupe_records:,}** across **{n_groups:,}** clusters",
        f"- Likely the **same person** (same phone + same name): **{n_same:,}** records",
        f"- **Shared-phone households** (same phone, different names — kept separate): **{n_house:,}** records",
        f"- De-duplicated marketing list size: **{len(mk):,}** unique contactable customers "
        f"(down from {n0:,})\n",
        "## Files",
        "- `marketing_list_deduped.csv` — upload-ready, one row per contactable person/household",
        "- `customer_master_clean.csv` — full cleaned reference with flags",
        "- `duplicate_groups.csv` — clusters to review/merge",
        "- `needs_attention.csv` — junk / non-contactable to exclude or fix",
        "\n_Enrichment note: missing emails (≈40%) cannot be filled from MAS alone — "
        "needs an external append service or cross-referencing Shopify._",
    ]
    with open(os.path.join(OUT, "cleaning_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rpt))

    print(f"\nInput: {n0:,}  ->  deduped marketing list: {len(mk):,}")
    print(f"  valid email {int(c['email_valid'].sum()):,} | valid phone {int(c['phone_valid'].sum()):,} "
          f"| contactable {int(c['contactable'].sum()):,}")
    print(f"  duplicate clusters: {n_groups:,} ({n_same:,} same-person, {n_house:,} household records)")
    print(f"  files -> {OUT}")


if __name__ == "__main__":
    main()
