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

## 4. Clone the warehouse (remote -> local dump file -> local restore)
Run pg_dump/pg_restore from inside the postgres:17 image so versions match.
NOTE: hits the production warehouse (read-only). Confirm before running.

```powershell
# Dump remote (data-only for the 3 MAS tables; schema comes too with --schema)
docker run --rm postgres:17 pg_dump `
  "host=db.<ref>.supabase.co port=5432 dbname=postgres user=postgres password=<pw>" `
  --no-owner --no-privileges `
  -t public.mas_customers -t public.mas_orders -t public.mas_products `
  -Fc -f /tmp/mas.dump   # (write to a mounted volume in practice)
```
(Exact, parameterized commands will be provided as a script once you approve the clone.)

## 5. Verify
```powershell
docker exec -it mas-pg psql -U postgres -d mas_warehouse -c "\dt"
docker exec -it mas-pg psql -U postgres -d mas_warehouse -c "SELECT count(*) FROM mas_orders;"
```

## Connectivity note
The Supabase **direct** host `db.<ref>.supabase.co:5432` needs IPv6 (or the IPv4
add-on). If it won't connect, use the **Session pooler**
(`aws-0-<region>.pooler.supabase.com:5432`, user `postgres.<ref>`) — it supports
`pg_dump`. The **transaction pooler** (port 6543) does NOT support `pg_dump`.
