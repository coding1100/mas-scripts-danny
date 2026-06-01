"""
Phase 5b — Product / basket affinity for cross-sell & upsell (LOCAL clone).

IMPORTANT caveats (the data is messy):
  - 80% of orders have a single line item, so "bought together" signal is thin.
  - "products" include non-products: discount lines, "pulled from cooler",
    "designer choice", "0.00". These are filtered out as NOISE.
  - product_id carries category-ish codes (VASE, WRAP, PHAL, COOL...) used for
    add-on ATTACH RATES, which is the most actionable output here.
  - This analysis needs only mas_products <-> mas_orders (NOT the customer link),
    so the 51% orphan-customer_id problem does NOT affect it.

Outputs (exports/affinity/):
  - top_products.csv          : most-sold cleaned items (count + revenue)
  - addon_attach_rates.csv    : how often each add-on category is attached
  - product_pairs.csv         : top co-occurring item pairs (support/confidence/lift)
  - affinity_report.md        : plain-language summary

Usage:
    python analysis/product_affinity.py [--store 001] [--min-pair 30]
"""
import argparse
import os
import re
import sys
from collections import Counter
from itertools import combinations

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import get_local_engine  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports", "affinity")
os.makedirs(OUT, exist_ok=True)

# Line items that are NOT real products -> drop (incl. B2B recurring-account templates)
NOISE = re.compile(r"(?:discount\s*pct|pulled from cooler|designer choice|wrapped bouquet|"
                   r"must include add on|^0+(?:\.0+)?$|^\s*$|delivery|service charge|^xxx|"
                   r"weekly billing|do not make|dining room|lobby|front desk|powder room|"
                   r"coffee table|reception desk|conference room|nurses station)", re.I)

# Add-on categories detected from product_id / description -> tracked as attach rate
ADDON_RULES = [
    ("Vase", re.compile(r"\bvase\b", re.I)),
    ("Wrap / hand-tied", re.compile(r"\bwrap\b", re.I)),
    ("Greeting card", re.compile(r"card\s*isle|greeting card|\bcard\b", re.I)),
    ("Balloon", re.compile(r"balloon", re.I)),
    ("Plush / teddy", re.compile(r"teddy|plush|bear", re.I)),
    ("Chocolate / candy", re.compile(r"chocolat|godiva|candy|truffle", re.I)),
    ("Giftware", re.compile(r"giftware", re.I)),
]


def classify_addon(text):
    t = text or ""
    for name, rx in ADDON_RULES:
        if rx.search(t):
            return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store")
    ap.add_argument("--min-pair", type=int, default=30, help="min co-occurrence count for a pair")
    args = ap.parse_args()
    engine = get_local_engine()

    print("Loading products + orders...")
    store_join = ""
    if args.store:
        store_join = ("JOIN mas_customers c ON trim(c.account_id)=trim(o.customer_id) "
                      f"AND trim(c.location)='{args.store}'")
    df = pd.read_sql(
        "SELECT p.order_id, p.product_id, p.description, "
        "       NULLIF(regexp_replace(p.extended_price,'[^0-9.\\-]','','g'),'')::numeric AS ext "
        "FROM mas_products p "
        "JOIN mas_orders o ON o.order_id=p.order_id AND lower(o.order_status)<>'cancelled' "
        f"{store_join}", engine)
    n_line = len(df)

    df["desc_norm"] = df["description"].astype("string").str.strip().str.lower()
    df["pid"] = df["product_id"].astype("string").str.strip().str.upper()
    df["is_noise"] = df["desc_norm"].fillna("").str.contains(NOISE, na=False) | (df["desc_norm"].fillna("") == "")
    df["addon"] = (df["pid"].fillna("") + " " + df["desc_norm"].fillna("")).map(classify_addon)

    clean = df[~df["is_noise"]].copy()
    # Item key for pairing: prefer category code, else normalized description
    clean["item"] = clean["addon"].fillna(clean["desc_norm"])

    # --- Top products ---
    top = clean.groupby("item").agg(line_items=("order_id", "count"),
                                    revenue=("ext", "sum")).round(0)
    top = top.sort_values("line_items", ascending=False).head(50)
    top.to_csv(os.path.join(OUT, "top_products.csv"))

    # --- Add-on attach rates (share of orders containing each add-on) ---
    n_orders = df["order_id"].nunique()
    addon_orders = (df[df["addon"].notna()].groupby("addon")["order_id"].nunique()
                    .sort_values(ascending=False))
    attach = (addon_orders / n_orders * 100).round(1).rename("attach_rate_pct").to_frame()
    attach["orders_with_addon"] = addon_orders
    attach.to_csv(os.path.join(OUT, "addon_attach_rates.csv"))

    # --- Pair co-occurrence (market basket) ---
    baskets = clean.groupby("order_id")["item"].apply(lambda s: sorted(set(s.dropna())))
    item_count = Counter()
    pair_count = Counter()
    multi = 0
    for items in baskets:
        for it in items:
            item_count[it] += 1
        if len(items) >= 2:
            multi += 1
            for a, b in combinations(items, 2):
                pair_count[(a, b)] += 1

    total_b = len(baskets)
    rows = []
    for (a, b), c in pair_count.items():
        if c < args.min_pair:
            continue
        pa, pb, pab = item_count[a] / total_b, item_count[b] / total_b, c / total_b
        lift = pab / (pa * pb) if pa and pb else 0
        rows.append({"item_a": a, "item_b": b, "pair_orders": c,
                     "support_pct": round(pab * 100, 3),
                     "confidence_a_to_b_pct": round(c / item_count[a] * 100, 1),
                     "lift": round(lift, 1)})
    pairs = pd.DataFrame(rows).sort_values(["pair_orders", "lift"], ascending=False)
    pairs.to_csv(os.path.join(OUT, "product_pairs.csv"), index=False)

    # --- Report ---
    rpt = [
        "# Product / Basket Affinity",
        f"_Scope: {'store ' + args.store if args.store else 'all stores'}  |  "
        f"{n_line:,} line items, {n_orders:,} non-cancelled orders_\n",
        "**Caveat:** ~80% of orders are single-item, and many line items are not real "
        "products (filtered out). Treat this as directional, strongest for add-ons.\n",
        "## Add-on attach rates (upsell opportunity)",
        "| Add-on | Orders with it | Attach rate |",
        "|---|---:|---:|",
    ]
    for name, r in attach.iterrows():
        rpt.append(f"| {name} | {int(r['orders_with_addon']):,} | {r['attach_rate_pct']}% |")
    rpt += ["\n## Top co-purchased pairs (by how often, after filtering B2B templates)",
            "| Item A | Item B | Orders | Confidence A→B | Lift |",
            "|---|---|---:|---:|---:|"]
    for _, r in pairs.head(15).iterrows():
        rpt.append(f"| {str(r['item_a'])[:40]} | {str(r['item_b'])[:40]} | {int(r['pair_orders'])} | "
                   f"{r['confidence_a_to_b_pct']}% | {r['lift']} |")
    rpt += ["\n## Files",
            "- `addon_attach_rates.csv` — what to bundle/upsell at checkout",
            "- `product_pairs.csv` — co-occurring items (support/confidence/lift)",
            "- `top_products.csv` — best sellers (cleaned)",
            "\n_Customer-targeted cross-sell (who to email a given product) would need the "
            "order→customer link, degraded by the 51% orphan-customer_id issue. These "
            "product-level results do not depend on it._"]
    with open(os.path.join(OUT, "affinity_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rpt))

    print(f"\nLine items: {n_line:,}  |  orders: {n_orders:,}  |  multi-item baskets: {multi:,}")
    print(f"Add-on attach rates:\n{attach.to_string()}")
    print(f"\nTop pairs (lift>=, min {args.min_pair} co-occurrences): {len(pairs):,}")
    print(pairs.head(12).to_string(index=False))
    print(f"\nfiles -> {OUT}")


if __name__ == "__main__":
    main()
