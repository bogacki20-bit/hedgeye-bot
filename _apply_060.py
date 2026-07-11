"""One-shot: apply migration 060 (report_rows) + render a live REPORT so you
can see exactly what Telegram will return (stores one on-demand row)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "060_report_rows.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM report_rows")
    print("report_rows ready, rows:", cur.fetchone()[0])

print("\n=== LIVE REPORT ===")
from tools.report import build_report, store_report
body = build_report()
print(body)
store_report(body, "on-demand")
print("\n(stored as on-demand row)")
