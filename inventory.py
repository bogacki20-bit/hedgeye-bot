import os, psycopg
from dotenv import load_dotenv
load_dotenv()

c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))
tables = c.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema='public' ORDER BY table_name
""").fetchall()

print(f"\n=== {len(tables)} TABLES IN POSTGRES ===\n")
for (t,) in tables:
    try:
        n = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    except Exception as e:
        c.rollback()
        n = f"? ({e})"
    print(f"  {t:<40} {n} rows")
c.close()
print()