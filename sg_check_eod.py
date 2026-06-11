import os
from dotenv import load_dotenv
load_dotenv()
import psycopg2
url = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
c = psycopg2.connect(url)
cur = c.cursor()
cur.execute("SELECT capture_type, COUNT(*) FROM spotgamma_snapshots WHERE snapshot_date = '2026-05-28' GROUP BY capture_type ORDER BY capture_type")
print("by_capture_type:", cur.fetchall())
cur.execute("SELECT COUNT(*) FROM spotgamma_snapshots WHERE snapshot_date = '2026-05-28'")
print("total_today:", cur.fetchone())
cur.execute("SELECT ticker FROM spotgamma_snapshots WHERE snapshot_date = '2026-05-28' AND capture_type='eod' ORDER BY ticker")
print("eod_tickers:", [r[0] for r in cur.fetchall()])
cur.close(); c.close()
