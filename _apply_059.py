"""One-shot: apply migration 059 (book_alerts_fired) + live DRY-RUN of the
book-alert detector against your actual holdings (sends nothing)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "059_book_alerts.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM book_alerts_fired")
    print("book_alerts_fired ready, rows:", cur.fetchone()[0])

print("\n=== book alerts DRY-RUN over live holdings (nothing sent) ===")
from tools.book_alerts import run_book_alerts
s = run_book_alerts(dry_run=True)
print(s)
