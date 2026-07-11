import os, psycopg
from dotenv import load_dotenv
load_dotenv()

c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))

cols = c.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name='bot_state' ORDER BY ordinal_position
""").fetchall()
print("\nbot_state columns:", ", ".join(col[0] for col in cols), "\n")

rows = c.execute("SELECT * FROM bot_state").fetchall()
colnames = [col[0] for col in cols]
for r in rows:
    print("---")
    for name, val in zip(colnames, r):
        s = str(val)
        if len(s) > 120:
            s = s[:120] + "..."
        print(f"  {name}: {s}")
c.close()
print()