"""
Apply schema.sql to the Postgres database referenced by DATABASE_URL.

Idempotent — safe to run multiple times. Each statement uses CREATE TABLE
IF NOT EXISTS / CREATE INDEX IF NOT EXISTS so re-runs are no-ops.

Usage (from C:\\Projects\\hedgeye-bot, with railway linked):
    railway run python apply_schema.py

The script:
  1. Reads DATABASE_URL from env (Railway injects it via `railway run`)
  2. Reads schema.sql from the same directory as this script
  3. Connects, runs the entire script in a single transaction
  4. Prints a list of tables that exist after apply, for verification
"""

import os
import sys
import pathlib

try:
    import psycopg2
except ImportError:
    sys.stderr.write(
        "psycopg2 not installed. Install with:\n"
        "    pip install psycopg2-binary\n"
        "(or add psycopg2-binary to requirements.txt and let Railway install it)\n"
    )
    sys.exit(1)


def main():
    # When running locally via `railway run`, DATABASE_URL points at
    # postgres.railway.internal which only resolves inside Railway's network.
    # DATABASE_PUBLIC_URL goes through Railway's TCP proxy and works from
    # outside. Prefer it if present, fall back to DATABASE_URL otherwise
    # (which is what the deployed bot will use).
    db_url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        sys.stderr.write("Neither DATABASE_PUBLIC_URL nor DATABASE_URL set in environment.\n")
        sys.exit(1)
    if "railway.internal" in db_url:
        sys.stderr.write(
            "DATABASE_URL points at railway.internal (only resolves inside Railway).\n"
            "Add DATABASE_PUBLIC_URL to this service in Railway dashboard:\n"
            "    Variables -> New Variable\n"
            "    Name:  DATABASE_PUBLIC_URL\n"
            "    Value: ${{Postgres.DATABASE_PUBLIC_URL}}\n"
            "Then re-run.\n"
        )
        sys.exit(1)

    schema_path = pathlib.Path(__file__).parent / "schema.sql"
    if not schema_path.exists():
        sys.stderr.write(f"schema.sql not found at {schema_path}\n")
        sys.exit(1)

    sql = schema_path.read_text(encoding="utf-8")
    print(f"Loaded {len(sql)} chars from {schema_path.name}")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            print("Applying schema...")
            cur.execute(sql)
        conn.commit()
        print("Schema applied. Verifying tables...")

        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            tables = [r[0] for r in cur.fetchall()]

        print(f"\n{len(tables)} table(s) in public schema:")
        for t in tables:
            print(f"  - {t}")

    except Exception as e:
        conn.rollback()
        sys.stderr.write(f"\nSchema apply failed (rolled back): {e}\n")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
