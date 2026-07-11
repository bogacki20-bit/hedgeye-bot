import os, psycopg
from dotenv import load_dotenv
load_dotenv()
c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))

# what columns do the two signal tables have?
for tbl in ("hedgeye_risk_ranges", "hedgeye_signal_strength"):
    cols = c.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s ORDER BY ordinal_position
    """, (tbl,)).fetchall()
    print(f"\n=== {tbl} columns ===")
    print(", ".join(col[0] for col in cols))
    # show 2 sample rows so we see the actual data shape
    rows = c.execute(f'SELECT * FROM "{tbl}" ORDER BY 1 DESC LIMIT 2').fetchall()
    for r in rows:
        print(r)
c.close()