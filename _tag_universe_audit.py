"""_tag_universe_audit.py — READ-ONLY: define the tagging build's target.

Universe = every ticker the bot can see (mfr_snapshots ∪ book ∪ all signal
sources) vs ticker_tags. Prints counts per origin, the untagged set, and a
PRIORITY tier (held or roster-member untagged names — they drive REPORT
NOW cues, fill tiers, and CONC, so they get tagged first).
    python _tag_universe_audit.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("SELECT ticker FROM ticker_tags")
    tagged = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT ticker FROM mfr_snapshots")
    mfr = {r[0] for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT underlying FROM book_positions
                   WHERE snapshot_date = (SELECT max(snapshot_date)
                                          FROM book_positions)
                     AND asset_class <> 'cash'""")
    book = {r[0] for r in cur.fetchall()}
    try:
        cur.execute("SELECT ticker FROM ss_roster_history "
                    "WHERE removed_on IS NULL")
        ss = {r[0] for r in cur.fetchall()}
    except Exception:
        ss = set()

sources = {}
try:
    from tools.source_registry import REGISTRY
    for s in REGISTRY:
        try:
            sources[s.tag] = set(s.members())
        except Exception as e:
            print(f"  (source {s.tag} unreadable: {e})")
except Exception as e:
    print(f"(source registry unavailable: {e})")

universe = mfr | book | ss | set().union(*sources.values()) if sources else \
    (mfr | book | ss)
untagged = sorted(universe - tagged)
priority = sorted((book | ss) - tagged)

print(f"universe: {len(universe)} names "
      f"(mfr {len(mfr)} · book {len(book)} · ss {len(ss)} · "
      + " · ".join(f"{k} {len(v)}" for k, v in sorted(sources.items())) + ")")
print(f"tagged: {len(tagged)} · UNTAGGED: {len(untagged)}")
print(f"\nPRIORITY untagged (held or SS roster, {len(priority)}):")
print("  " + (" ".join(priority) or "none"))
rest = sorted(set(untagged) - set(priority))
print(f"\nremaining untagged ({len(rest)}):")
for i in range(0, len(rest), 18):
    print("  " + " ".join(rest[i:i + 18]))
