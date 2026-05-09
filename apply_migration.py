"""
Apply a single SQL migration file to the Postgres database.

Usage:
  $env:DATABASE_PUBLIC_URL = "postgresql://..."
  py apply_migration.py migrations/002_dry_run_framework.sql

Reads the SQL file, opens a single transaction, executes it, commits.
Idempotent migrations (CREATE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)
are safe to re-run.
"""

import os
import sys

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. Run: py -m pip install psycopg2-binary")
    sys.exit(2)


def main():
    if len(sys.argv) < 2:
        print("usage: py apply_migration.py <migration.sql>")
        sys.exit(2)

    migration_path = sys.argv[1]
    if not os.path.exists(migration_path):
        print(f"ERROR: file not found: {migration_path}")
        sys.exit(2)

    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: set DATABASE_PUBLIC_URL or DATABASE_URL env var first.")
        print("  Find it in: railway.app -> wonderful-tranquility -> Postgres service -> Variables")
        sys.exit(2)

    with open(migration_path, encoding="utf-8") as f:
        sql = f.read()

    print(f"Applying {migration_path} ({len(sql)} bytes) to {url[:30]}...")
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print(f"OK: migration applied.")
    except Exception as e:
        conn.rollback()
        print(f"FAILED: {e}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
