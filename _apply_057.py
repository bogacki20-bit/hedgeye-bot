"""One-shot: apply migration 057 (vol_regime_daily), backfill DRY-RUN preview,
then if it looks sane run the real backfill:
    python -m tools.vol_regime --backfill
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "057_vol_regime_daily.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM vol_regime_daily")
    print("vol_regime_daily ready, rows:", cur.fetchone()[0])

print("\n=== vol-regime backfill DRY-RUN ===")
from tools.vol_regime import backfill, regime_line
backfill(dry_run=True)
