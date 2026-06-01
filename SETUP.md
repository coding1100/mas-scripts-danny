# Local Setup — MAS Data Warehouse Analysis

All analysis runs against a **local Postgres clone** of the remote Supabase
"Data Warehouse". We never write to the production warehouse.

## Prerequisites
- Python 3.11+ (have: 3.11.9)
- Docker (have: 29.3.1)
- No native Postgres needed — Docker provides version-matched `pg_dump`/`psql`.

## 1. Python deps
```powershell
pip install -r requirements.txt
```

## 2. Credentials
Copy `.env.example` to `.env` and fill it in. `.env` is gitignored — never commit it.
- `SOURCE_DB_*` = remote Supabase (read-only; clone only)
- `LOCAL_DB_*`  = local Docker Postgres (analysis target)

## 3. Start the local Postgres (Docker)
```powershell
# NOTE: host port 5433 (another Postgres already owns 5432 on this machine).
# .env -> LOCAL_DB_PORT must match (5433). Image is postgres:15 to match server 15.8.
docker run -d --name mas-pg `
  -e POSTGRES_PASSWORD=localdev `
  -e POSTGRES_DB=mas_warehouse `
  -p 5433:5432 `
  -v mas-pg-data:/var/lib/postgresql/data `
  postgres:15
```

## 4. Clone the warehouse (remote -> local, streamed)
Stream `pg_dump` (remote, via the session pooler) straight into `pg_restore`
(local). Run from the `postgres:15` image so versions match the server (15.8).
NOTE: reads the production warehouse (read-only via the `mas_readonly` role).

```bash
# Values come from .env (SOURCE_DB_*). The session pooler is required (see note below).
CONN="host=aws-0-us-east-1.pooler.supabase.com port=5432 dbname=postgres \
user=mas_readonly.<project-ref> password=<pw>"

docker run --rm postgres:15 pg_dump "$CONN" -Fc --no-owner --no-privileges \
  -t public.mas_customers -t public.mas_orders -t public.mas_products \
  | docker exec -i mas-pg pg_restore -U postgres -d mas_warehouse --no-owner --no-privileges
```

## 5. Verify
```powershell
docker exec -it mas-pg psql -U postgres -d mas_warehouse -c "\dt"
docker exec -it mas-pg psql -U postgres -d mas_warehouse -c "SELECT count(*) FROM mas_orders;"
```

## 6. Run the analysis scripts
All scripts are **read-only**, connect to the LOCAL clone via `db.py`, and write
results to `exports/` (gitignored — outputs contain customer PII). Run from the
repo root after steps 1–5.

```powershell
# A) Data quality audit — what's in the list, what's missing, store/date coverage
python analysis/data_quality_audit.py
#   options: --store 001        (one store only)
#            --start 2020-01-01 --end 2025-12-31   (filter orders by date)
#   outputs: exports/data_quality_report.md, store_summary.csv, orders_by_year.csv

# B) RFM segmentation — VIP / Champions / At-Risk / lapsed win-back lists
python analysis/rfm_segmentation.py
#   options: --store 001
#            --asof 2025-05-19          (recency anchor; default = latest purchase in data)
#            --lapsed-months 12         (lapsed threshold)
#            --vip-percentile 95        (top X% = VIP)
#   outputs: exports/rfm_report.md, rfm_all_customers.csv, rfm_segment_summary.csv,
#            exports/segments/*.csv   (upload-ready per-segment lists)

# C) Customer list cleaning + de-duplication -> marketing master
python analysis/clean_customer_list.py
#   options: --store 001
#   outputs: exports/clean/marketing_list_deduped.csv, customer_master_clean.csv,
#            duplicate_groups.csv, needs_attention.csv, cleaning_report.md

# D) Product / basket affinity — add-on attach rates + co-purchase pairs
python analysis/product_affinity.py
#   options: --store 001
#            --min-pair 30              (min co-occurrence count to report a pair)
#   outputs: exports/affinity/addon_attach_rates.csv, product_pairs.csv,
#            top_products.csv, affinity_report.md
```

Each script prints a console summary and writes a plain-language `*.md` report
(for non-technical stakeholders) plus detailed CSVs (for the team / CRM upload).

## Connectivity note
The Supabase **direct** host `db.<ref>.supabase.co:5432` needs IPv6 (or the IPv4
add-on). If it won't connect, use the **Session pooler**
(`aws-0-<region>.pooler.supabase.com:5432`, user `postgres.<ref>`) — it supports
`pg_dump`. The **transaction pooler** (port 6543) does NOT support `pg_dump`.
