"""_desc_audit.py — READ-ONLY: dump every held underlying's Fidelity
description + the asset class the current default_target regexes assign.
Used to tune classification against REAL Fidelity abbreviations (TREAS,
BD, FD, issuer names) before committing TRANCHE v2. Writes nothing.
    python _desc_audit.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg
from tools.position_targets import default_target
from tools.book_direction import book_sides

sides = book_sides()
with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""
        SELECT underlying, max(description)
        FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
          AND asset_class <> 'cash' AND COALESCE(quantity, 0) <> 0
        GROUP BY underlying ORDER BY underlying""")
    rows = cur.fetchall()

print(f"{len(rows)} held underlyings\n")
print(f"{'tkr':<8}{'class':<12}{'side':<7}description")
for t, d in rows:
    side = (sides.get(t) or {}).get("side") or "?"
    pct, label = default_target(d, side if side in ("long", "short") else None)
    print(f"{t:<8}{label:<12}{side:<7}{(d or 'NO DESCRIPTION')[:70]}")
