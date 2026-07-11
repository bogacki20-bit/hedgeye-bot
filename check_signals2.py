import os, psycopg
from dotenv import load_dotenv
load_dotenv()
c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))

names = ('META','URA','AAPL','AMZN','NFLX','TSLA','MSFT','GOOGL')
print("\n=== most recent risk range per ticker ===")
for t in names:
    row = c.execute("""
        SELECT ticker, signal_date, trend, buy_trade, sell_trade, prev_close
        FROM hedgeye_risk_ranges
        WHERE ticker = %s
        ORDER BY signal_date DESC LIMIT 1
    """, (t,)).fetchone()
    if row:
        print(f"{row[0]:6} {row[1]}  {row[2]:8}  range {row[3]}-{row[4]}  prev {row[5]}")
    else:
        print(f"{t:6} -- no rows --")

# how fresh is the table overall?
latest = c.execute("SELECT MAX(signal_date) FROM hedgeye_risk_ranges").fetchone()[0]
print(f"\nnewest signal_date in whole table: {latest}")
c.close()