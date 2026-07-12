"""_apply_065.py — T1A deep parse: migration 065 (t1a_daily) + parse of
every stored tier1alpha upload (backfill from doc_uploads).

    python _apply_065.py            # migration + DRY RUN: shows what each
                                    #   stored T1A upload parses into.
    python _apply_065.py --commit   # writes the t1a_daily rows.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

commit = "--commit" in sys.argv
skip = set()
if "--skip" in sys.argv:
    i = sys.argv.index("--skip")
    skip = {int(x) for x in sys.argv[i + 1:] if x.isdigit()}

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "065_t1a_daily.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM t1a_daily")
    print("t1a_daily ready, rows:", cur.fetchone()[0])
    cur.execute("""SELECT id, note_date, content_text FROM doc_uploads
                   WHERE kind = 'tier1alpha' ORDER BY id""")
    docs = cur.fetchall()

from tools.t1a_parse import parse_t1a, fact_line, store_t1a

if not docs:
    sys.exit("no tier1alpha uploads stored yet — send one, then rerun.")
for doc_id, nd, text in docs:
    if doc_id in skip:
        print(f"\n[doc {doc_id}] SKIPPED (operator --skip)")
        continue
    p = parse_t1a(text or "")
    p["report_date"] = nd
    print(f"\n[doc {doc_id} · note_date {nd or 'UNDATED ⚠'}]")
    print("  " + fact_line(p))
    missing = [k for k in ("gamma_regime", "last_price", "gex_flip",
                           "systematic_bias", "strategic_regime")
               if p.get(k) is None]
    if missing:
        print("  ⚠ missing fields: " + " ".join(missing))
    if commit:
        if nd is None:
            print("  SKIPPED write — undated (a fact without a date)")
        elif store_t1a(p, nd, doc_id):
            print("  ✅ written to t1a_daily")
        else:
            print("  🛑 store failed")
if not commit:
    print("\nDry run. If the fact lines read right:")
    print("    python _apply_065.py --commit")
