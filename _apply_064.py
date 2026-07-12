"""_apply_064.py — sprint P2: doc_uploads table (Telegram document ingest).

    python _apply_064.py            # apply migration (idempotent) + smoke-
                                    #   test the classifier on sample names.
                                    #   No data writes.

No --commit variant: the table starts empty by design — rows arrive when
the operator sends files to the bot. The send is the operator action.
After the Railway deploy, test from the phone: send any PDF/txt to the
bot; expect a '📥 stored: … kind=… date=…' reply. pypdf ships in
requirements — PDFs extract on the bot, no local install needed.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "064_doc_uploads.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM doc_uploads")
    print("doc_uploads ready, rows:", cur.fetchone()[0])

from tools.doc_ingest import classify_upload, parse_note_date

print("\nclassifier smoke (filename -> kind · parsed date):")
for fn in ("founders_note_am_2026-07-11.pdf",
           "SpotGamma Founders Note PM.pdf",
           "flow_patrol_jul11.pdf",
           "EquityHub_export_2026-07-11.csv",
           "Tier One Alpha 7-11.pdf",
           "random_screenshot.jpg"):
    print(f"  {fn:<42} {classify_upload(fn, ''):<18} "
          f"{parse_note_date(fn, '') or 'undated'}")

print("\nDone. Deploy, then send a real file to the bot from the phone.")
