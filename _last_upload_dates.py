"""READ-ONLY: last upload dates for setting the Fidelity export range."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if not (os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")):
    for ln in open(os.path.join(os.path.dirname(__file__), ".env")):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ.setdefault(k, v)

import db_pg

QUERIES = [
    ("positions (book_positions)",  "SELECT max(snapshot_date) FROM book_positions"),
    ("trade history (book_activity)", "SELECT max(run_date), count(*) FROM book_activity"),
    ("ML trades (actions_log)",     "SELECT max(run_date), count(*) FROM actions_log"),
]

with db_pg.get_conn() as conn, conn.cursor() as cur:
    for label, sql in QUERIES:
        cur.execute(sql)
        print(f"{label:<32} {cur.fetchone()}")
