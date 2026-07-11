"""One-shot: apply migration 058 (ss_flow_events) + backfill DRY-RUN preview.
After eyeballing:  python -m tools.ss_flow --backfill
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "058_ss_flow_events.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM ss_flow_events")
    print("ss_flow_events ready, rows:", cur.fetchone()[0])

print("\n=== SS flow backfill DRY-RUN ===")
from tools.ss_flow import stamp_unstamped
print(stamp_unstamped(dry_run=True))
