"""_doc_uploads_check.py — READ-ONLY: list the latest doc_uploads rows
(did my upload land, what kind/date/chars did it get?). Writes nothing.
    python _doc_uploads_check.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""SELECT id, uploaded_at, file_name, kind, note_date,
                          char_count, left(coalesce(content_text, ''), 120)
                   FROM doc_uploads ORDER BY uploaded_at DESC LIMIT 10""")
    rows = cur.fetchall()

if not rows:
    print("doc_uploads is EMPTY — nothing has landed.")
for rid, at, fn, kind, nd, chars, head in rows:
    print(f"[{rid}] {str(at)[:19]}  {fn or '?'}")
    print(f"     kind={kind} · date={nd or 'UNDATED'} · {chars or 0:,} chars")
    print(f"     head: {' '.join((head or '').split())}\n")
