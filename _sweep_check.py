import os
from dotenv import load_dotenv
import psycopg2
load_dotenv()
url = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
c = psycopg2.connect(url)
cur = c.cursor()

# hedgeye_ticker_inventory by last_source on 2026-05-11
cur.execute("SELECT last_source, count(*) FROM hedgeye_ticker_inventory WHERE last_seen_at::date='2026-05-11' GROUP BY last_source ORDER BY count(*) DESC")
print('hedgeye_last_seen_2026_05_11:', cur.fetchall())
cur.execute("SELECT last_source, count(*) FROM hedgeye_ticker_inventory WHERE last_seen_at::date='2026-05-12' GROUP BY last_source ORDER BY count(*) DESC")
print('hedgeye_last_seen_2026_05_12:', cur.fetchall())
cur.execute("SELECT last_source, count(*) FROM hedgeye_ticker_inventory GROUP BY last_source ORDER BY count(*) DESC LIMIT 15")
print('hedgeye_all_by_source:', cur.fetchall())

# Tier-1 spotgamma — define common tier1 and check
tier1 = ['SPY','QQQ','IWM','TLT','GLD','SLV','UUP','VIX','SPX','NDX','RUT','DIA','HYG','LQD','XLF']
cur.execute("SELECT ticker, max(snapshot_date) FROM spotgamma_snapshots WHERE ticker = ANY(%s) GROUP BY ticker ORDER BY ticker", (tier1,))
print('tier1_spotgamma_max:', cur.fetchall())

cur.close(); c.close()
