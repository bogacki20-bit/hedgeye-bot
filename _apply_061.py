"""_apply_061.py — REPORT v4: apply migration 061 (report_snapshots) +
DRY-RUN render so you can eyeball exactly what Telegram will return.

    python _apply_061.py            # migration + dry-run render (NO snapshot
                                    #   write, NO report_rows write)
    python _apply_061.py --commit   # real run: renders v4, stores the first
                                    #   snapshot + an on-demand report row

The migration itself (CREATE TABLE IF NOT EXISTS) runs in both modes — it is
idempotent and creates report infrastructure only, no signal tables.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

commit = "--commit" in sys.argv

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "061_report_snapshots.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM report_snapshots")
    print("report_snapshots ready, rows:", cur.fetchone()[0])

from tools.report import build_report_v4, build_report_legacy, store_report

print("\n=== REPORT v4 (%s) ===" % ("REAL RUN — snapshot stored" if commit
                                    else "DRY RUN — nothing stored"))
body = build_report_v4(kind="on-demand", persist_snapshot=commit)
print(body)
print(f"\n[{len(body)} chars — Telegram chunks at 4096 on line boundaries]")

if commit:
    store_report(body, "on-demand")
    print("(stored: report_rows on-demand + first report_snapshots state)")
else:
    print("\n=== REPORT LEGACY header check (first 3 lines) ===")
    print("\n".join(build_report_legacy().split("\n")[:3]))
    print("\nDry run only. Eyeball the sections above, then:")
    print("    python _apply_061.py --commit")
