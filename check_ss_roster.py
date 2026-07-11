import os, psycopg
from dotenv import load_dotenv
load_dotenv()
c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))

# latest snapshot date
latest = c.execute("SELECT MAX(snapshot_date) FROM hedgeye_signal_strength").fetchone()[0]
print("latest snapshot_date:", latest)

# how does the bot represent "currently on the list"? show distinct flag combos on latest date
print("\nrows on latest date by flag:")
for r in c.execute("""
    SELECT is_added_today, is_removed_today, COUNT(*)
    FROM hedgeye_signal_strength WHERE snapshot_date=%s
    GROUP BY 1,2""",(latest,)).fetchall():
    print("  added:",r[0]," removed:",r[1]," count:",r[2])

# total distinct tickers ever seen vs today's removed
total = c.execute("SELECT COUNT(DISTINCT ticker) FROM hedgeye_signal_strength").fetchone()[0]
print(f"\ndistinct tickers ever in table: {total}")

# today's adds/removes explicitly
print("\nadded today:", [r[0] for r in c.execute("SELECT ticker FROM hedgeye_signal_strength WHERE snapshot_date=%s AND is_added_today",(latest,)).fetchall()])
print("removed today:", [r[0] for r in c.execute("SELECT ticker FROM hedgeye_signal_strength WHERE snapshot_date=%s AND is_removed_today",(latest,)).fetchall()])
c.close()