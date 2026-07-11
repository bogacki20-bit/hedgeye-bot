"""One-shot: apply migrations/056_ps_flow_events.sql (idempotent), then run
the PS flow backfill in DRY-RUN so you can eyeball the add/drop history
before writing it. After eyeballing:
    python -m tools.ps_flow --backfill
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "056_ps_flow_events.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM ps_flow_events")
    print("ps_flow_events ready, rows:", cur.fetchone()[0])

print("\n=== PS flow backfill DRY-RUN (nothing written) ===")
from tools.ps_flow import backfill
backfill(dry_run=True)
