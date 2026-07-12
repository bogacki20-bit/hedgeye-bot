"""_retag_docs.py — one-shot: retag the two pre-pattern-fix doc_uploads
rows (SPX_data-table_*.csv, stored kind='other' before 'data-table' was a
known Equity Hub naming) to kind='equity_hub'.

    python _retag_docs.py            # DRY RUN — shows what would change
    python _retag_docs.py --commit   # applies
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

commit = "--commit" in sys.argv
with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""SELECT id, file_name, kind FROM doc_uploads
                   WHERE kind = 'other' AND file_name ILIKE '%%data-table%%'
                   ORDER BY id""")
    rows = cur.fetchall()
    if not rows:
        sys.exit("nothing to retag — no kind='other' data-table rows.")
    for rid, fn, kind in rows:
        print(f"[{rid}] {fn}: {kind} -> equity_hub"
              + ("" if commit else "   (dry run)"))
    if commit:
        cur.execute("""UPDATE doc_uploads SET kind = 'equity_hub'
                       WHERE kind = 'other' AND file_name ILIKE '%%data-table%%'""")
        print(f"retagged {cur.rowcount} row(s).")
        c.commit()
    else:
        print("\nDry run. Apply with: python _retag_docs.py --commit")
