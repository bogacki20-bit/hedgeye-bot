"""_tag_backfill.py — instrument (etf|stock|…) backfill for ticker_tags rows
that predate migration 066 (the original operator CSV load), plus any row
bucket-sync inserted with only a bucket. Reuses _tag_proposals' rule-based
classification, paced yfinance fetch, and cache.

Fills NULL columns ONLY — instrument always; gics_sector/subsector only
where currently NULL. An existing operator value is NEVER overwritten.

    python _tag_backfill.py             # DRY RUN (fetch + cache)
    python _tag_backfill.py --commit    # write the cached proposals
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _tag_proposals import (OPERATOR_OVERRIDES, _load_cache, _save_cache,
                            build_proposals, print_table)

commit = "--commit" in sys.argv

import db_pg

with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute("""SELECT ticker, gics_sector, subsector FROM ticker_tags
                   WHERE instrument IS NULL ORDER BY ticker""")
    targets = cur.fetchall()

if not targets:
    sys.exit("nothing to backfill — every ticker_tags row has an instrument.")

existing = {t: (g, s) for t, g, s in targets}
names = [t for t, _, _ in targets]
print(f"backfill targets (instrument IS NULL): {len(names)}")

cache = _load_cache()
rows = build_proposals(names, cache)
print_table("BACKFILL PROPOSALS", rows)

writable = [r for r in rows if r["instrument"]]
blocked = [r for r in rows if not r["instrument"]]
print(f"\nwritable: {len(writable)} · blocked (no instrument): {len(blocked)}")
for r in blocked:
    print(f"  🛑 {r['ticker']}: {r['src']} — left NULL")

if not commit:
    print("\nDry run — nothing written. If the table reads right:")
    print("    python _tag_backfill.py --commit")
    sys.exit(0)

filled_sector = 0
with db_pg.get_conn() as conn, conn.cursor() as cur:
    for r in writable:
        cur_g, cur_s = existing.get(r["ticker"], (None, None))
        cur.execute("UPDATE ticker_tags SET instrument=%s WHERE ticker=%s "
                    "AND instrument IS NULL", (r["instrument"], r["ticker"]))
        if cur_g is None and r["gics_sector"]:
            cur.execute("UPDATE ticker_tags SET gics_sector=%s "
                        "WHERE ticker=%s AND gics_sector IS NULL",
                        (r["gics_sector"], r["ticker"]))
            filled_sector += 1
        if cur_s is None and r["subsector"]:
            cur.execute("UPDATE ticker_tags SET subsector=%s "
                        "WHERE ticker=%s AND subsector IS NULL",
                        (r["subsector"], r["ticker"]))
    conn.commit()
print(f"\n✅ instrument set on {len(writable)} rows · "
      f"gics_sector filled on {filled_sector} previously-NULL rows · "
      f"{len(blocked)} left NULL (loud above). "
      f"No existing values touched.")
