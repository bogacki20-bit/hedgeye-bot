"""_doc_dump.py — READ-ONLY: print a doc_uploads row's full stored text
(design parsers against REAL captured content, not guesses).
    python _doc_dump.py <id>
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

if len(sys.argv) < 2 or not sys.argv[1].isdigit():
    sys.exit("usage: python _doc_dump.py <id>   (ids via _doc_uploads_check.py)")

with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("SELECT file_name, kind, note_date, char_count, content_text "
                "FROM doc_uploads WHERE id = %s", (int(sys.argv[1]),))
    row = cur.fetchone()
if not row:
    sys.exit(f"no doc_uploads row id {sys.argv[1]}")
fn, kind, nd, chars, text = row
print(f"[{sys.argv[1]}] {fn} · kind={kind} · date={nd} · {chars:,} chars")
print("=" * 70)
print(text or "(empty)")
