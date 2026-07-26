"""Fixture tests for screener pure logic (no DB) — the 'everything' lens parse
and the long-output auto-attach. Run: python test_screener.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
from tools.screener import parse_query, _maybe_attach, _snap_staleness, _rp_str

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'OK' if ok else 'XX'} {label}: got {got!r}" + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


print("parse_query — 'everything' lens:")
check("'everything longs' -> everything", parse_query("everything longs")["everything"], True)
check("'everything longs' direction", parse_query("everything longs")["direction"], "longs")
check("'universe shorts' -> everything", parse_query("universe shorts")["everything"], True)
check("'all assets longs' -> everything", parse_query("all assets longs")["everything"], True)
check("'energy longs' -> NOT everything", parse_query("energy longs")["everything"], False)
check("'everything' consumed (no unrecognized)",
      parse_query("everything longs")["unrecognized"], [])
check("'all names shorts' consumed clean",
      parse_query("all names shorts")["unrecognized"], [])

print("_maybe_attach — long output auto-attaches:")
short = "🔎 SCREEN — LONGS\n\n3 match(es)\nAAA\nBBB\nCCC"
check("short result stays inline (str)", isinstance(_maybe_attach(short, {"direction": "longs"}), str), True)

long_rows = "\n".join(f"ROW{i}  ticker subsector trend rp" for i in range(60))
res = _maybe_attach(long_rows, {"direction": "longs", "everything": True})
check("long result -> document dict", isinstance(res, dict), True)
check("doc has document_text", res.get("document_text") == long_rows, True)
check("doc name reflects scope", "everything" in res.get("document_name", ""), True)
check("doc name reflects direction", "longs" in res.get("document_name", ""), True)

check("non-str (guard) passes through",
      _maybe_attach({"already": "dict"}, {}), {"already": "dict"})

print("_snap_staleness — range (not price) freshness, fail-loud gate:")
MON = date(2026, 7, 27)      # a Monday
FRI = date(2026, 7, 24)      # prior Friday
# fresh: this name's snapshot == the freshest in the screen
check("fresh (sd==batch_max==today) -> not stale",
      _snap_staleness(MON, MON, MON), (False, 0))
# lagging: 3 days behind the freshest name in the same screen -> STALE (the USO case)
check("lags freshest by 3d -> STALE",
      _snap_staleness(FRI, MON, MON), (True, 3))
# weekend-safe: on Monday the freshest snapshot is Friday for EVERYONE, so a
# Friday snapshot does NOT flag even though it's 3 calendar days old
check("weekend-safe: Fri snap, Fri is freshest, on Mon -> not stale",
      _snap_staleness(FRI, FRI, MON), (False, 3))
# whole feed dark: freshest snapshot itself is 7 days old -> everything STALE
check("feed dark (freshest 7d old) -> STALE",
      _snap_staleness(date(2026, 7, 20), date(2026, 7, 20), MON), (True, 7))
# no snapshot at all -> STALE
check("no snapshot -> STALE", _snap_staleness(None, MON, MON), (True, None))

print("_rp_str — stale range suppresses the number (never a clean rp):")
check("stale range -> ⚠STALE(3d), NOT a number",
      _rp_str({"_snap_stale": True, "_snap_age_days": 3, "range_pos": 0.60}),
      "⚠STALE(3d)")
check("stale, no snapshot -> ⚠STALE(no-snap)",
      _rp_str({"_snap_stale": True, "_snap_age_days": None}), "⚠STALE(no-snap)")
check("fresh range, live price -> clean number",
      _rp_str({"_snap_stale": False, "range_pos": 0.60, "_rp_stale": False}), "0.60")
check("fresh range, no live price -> number + !eod (unchanged)",
      _rp_str({"_snap_stale": False, "range_pos": 0.60, "_rp_stale": True}), "0.60!eod")
check("no range_pos, not stale -> n/a",
      _rp_str({"_snap_stale": False, "range_pos": None}), "n/a")

print(f"\n{str(FAIL) + ' FAILURES' if FAIL else 'all passed'}")
sys.exit(1 if FAIL else 0)
