"""Print today's spotgamma_snapshots rows count + ticker list."""
import sys
sys.path.insert(0, r"C:\Projects\hedgeye-bot")
from database import _pg

con = _pg()
cur = con.cursor()
cur.execute("SELECT COUNT(*) FROM spotgamma_snapshots WHERE snapshot_date = current_date")
count = cur.fetchone()[0]
print(f"today_rows={count}")
cur.execute("SELECT ticker FROM spotgamma_snapshots WHERE snapshot_date = current_date ORDER BY ticker")
tickers = [r[0] for r in cur.fetchall()]
print(f"today_tickers={tickers}")
