"""Print today's spotgamma_snapshots rows count + ticker list.

    railway run py -m tools.sg_snapshots_today

11/19: imported database._pg (gone) from C:\\Projects\\hedgeye-bot; now db_pg like the rest of tools/.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_pg  # noqa: E402

with db_pg.get_conn() as con, con.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM spotgamma_snapshots WHERE snapshot_date = current_date")
    print(f"today_rows={cur.fetchone()[0]}")
    cur.execute("SELECT ticker FROM spotgamma_snapshots WHERE snapshot_date = current_date ORDER BY ticker")
    print(f"today_tickers={[r[0] for r in cur.fetchall()]}")
