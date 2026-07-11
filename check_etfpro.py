import os, psycopg
from dotenv import load_dotenv
load_dotenv()
c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))

# columns first
cols = c.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'hedgeye_etf_pro_ranges' ORDER BY ordinal_position
""").fetchall()
print("hedgeye_etf_pro_ranges columns:", ", ".join(col[0] for col in cols), "\n")

# anything for URA, SOLZ, TUR recently?
for t in ('URA','SOLZ','TUR'):
    rows = c.execute("""
        SELECT * FROM hedgeye_etf_pro_ranges
        WHERE ticker = %s ORDER BY 1 DESC LIMIT 2
    """, (t,)).fetchall()
    print(f"--- {t} ---")
    for r in rows:
        print(r)
    if not rows:
        print("  (no rows)")
c.close()