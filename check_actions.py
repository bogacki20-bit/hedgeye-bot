import os, psycopg
from dotenv import load_dotenv
load_dotenv()
c = psycopg.connect(os.getenv("DATABASE_PUBLIC_URL"))

def dump(tbl):
    cols = c.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name=%s ORDER BY ordinal_position""",(tbl,)).fetchall()
    colnames = [col[0] for col in cols]
    print(f"\n=== {tbl} ===")
    print("cols:", ", ".join(colnames))
    # pick a date column that exists
    datecol = next((d for d in ("captured_at","parsed_at","action_date","change_date","snapshot_date","date") if d in colnames), colnames[0])
    try:
        rows = c.execute(f'SELECT * FROM "{tbl}" ORDER BY {datecol} DESC LIMIT 5').fetchall()
        for r in rows:
            print(r)
    except Exception as e:
        c.rollback()
        print("err:", e)

dump("hedgeye_portfolio_actions")
dump("hedgeye_signal_changes")
c.close()