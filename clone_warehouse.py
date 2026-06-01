"""
One-command clone of the MAS Data Warehouse into a local Docker Postgres.

Reads ALL credentials from .env (SOURCE_DB_* = remote, LOCAL_DB_* = local), so
there are no placeholders to edit and the password's special characters are
handled correctly. Portable: works anywhere Docker + Python are installed.

    python clone_warehouse.py            # create local container (if needed) + clone
    python clone_warehouse.py --fresh    # destroy & recreate the local DB first

Reads from production READ-ONLY (via the mas_readonly role). Never writes remote.
"""
import argparse
import os
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()

CONTAINER = "mas-pg"
IMAGE = "postgres:15"          # matches server (15.x)
TABLES = ["public.mas_customers", "public.mas_orders", "public.mas_products"]


def env(key):
    v = os.getenv(key)
    if not v:
        sys.exit(f"Missing {key} in .env (copy .env.example -> .env and fill it in).")
    return v


def run(cmd, **kw):
    print("  $", " ".join(cmd[:3]), "..." if len(cmd) > 3 else "")
    return subprocess.run(cmd, **kw)


def container_exists():
    out = subprocess.run(["docker", "ps", "-aq", "-f", f"name=^{CONTAINER}$"],
                         capture_output=True, text=True).stdout.strip()
    return bool(out)


def ensure_local_db(fresh):
    port = os.getenv("LOCAL_DB_PORT", "5433")
    name = env("LOCAL_DB_NAME")
    pw = env("LOCAL_DB_PASSWORD")
    if fresh and container_exists():
        print("Removing existing container + data volume (--fresh)...")
        run(["docker", "rm", "-f", CONTAINER], capture_output=True)
        run(["docker", "volume", "rm", "mas-pg-data"], capture_output=True)
    if not container_exists():
        print(f"Starting local Postgres ({IMAGE}) on port {port}...")
        run(["docker", "run", "-d", "--name", CONTAINER,
             "-e", f"POSTGRES_PASSWORD={pw}",
             "-e", f"POSTGRES_DB={name}",
             "-p", f"{port}:5432",
             "-v", "mas-pg-data:/var/lib/postgresql/data", IMAGE], capture_output=True)
    else:
        run(["docker", "start", CONTAINER], capture_output=True)
    # wait until ready
    for _ in range(30):
        if subprocess.run(["docker", "exec", CONTAINER, "pg_isready", "-U", env("LOCAL_DB_USER")],
                          capture_output=True).returncode == 0:
            print("Local Postgres is ready.")
            return
        time.sleep(1)
    sys.exit("Local Postgres did not become ready in time.")


def source_conn():
    return (f"host={env('SOURCE_DB_HOST')} port={os.getenv('SOURCE_DB_PORT', '5432')} "
            f"dbname={env('SOURCE_DB_NAME')} user={env('SOURCE_DB_USER')} "
            f"password={env('SOURCE_DB_PASSWORD')}")


def clone():
    name = env("LOCAL_DB_NAME")
    user = env("LOCAL_DB_USER")
    dump_cmd = ["docker", "run", "--rm", "-e", "PGCONNECT_TIMEOUT=30", IMAGE,
                "pg_dump", source_conn(), "-Fc", "--no-owner", "--no-privileges"]
    for t in TABLES:
        dump_cmd += ["-t", t]
    # --clean --if-exists makes re-runs idempotent (drops & recreates the 3 tables)
    restore_cmd = ["docker", "exec", "-i", CONTAINER, "pg_restore",
                   "-U", user, "-d", name, "--no-owner", "--no-privileges",
                   "--clean", "--if-exists"]

    print("Cloning (remote pg_dump -> local pg_restore)... this can take a few minutes.")
    dump = subprocess.Popen(dump_cmd, stdout=subprocess.PIPE)
    restore = subprocess.Popen(restore_cmd, stdin=dump.stdout)
    dump.stdout.close()
    restore.communicate()
    if dump.wait() != 0:
        sys.exit("pg_dump failed (check SOURCE_DB_* creds / session pooler host).")
    if restore.returncode != 0:
        sys.exit("pg_restore failed.")


def verify():
    name, user = env("LOCAL_DB_NAME"), env("LOCAL_DB_USER")
    sql = ("SELECT 'mas_customers', count(*) FROM mas_customers "
           "UNION ALL SELECT 'mas_orders', count(*) FROM mas_orders "
           "UNION ALL SELECT 'mas_products', count(*) FROM mas_products;")
    print("\nLocal row counts:")
    run(["docker", "exec", CONTAINER, "psql", "-U", user, "-d", name, "-c", sql])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="destroy & recreate the local DB first")
    args = ap.parse_args()
    ensure_local_db(args.fresh)
    clone()
    verify()
    print("\nDone. Analysis scripts now read this local clone via db.py.")


if __name__ == "__main__":
    main()
