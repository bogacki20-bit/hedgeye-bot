"""Fixture tests for screener pure logic (no DB) — the 'everything' lens parse
and the long-output auto-attach. Run: python test_screener.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.screener import parse_query, _maybe_attach

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

print(f"\n{str(FAIL) + ' FAILURES' if FAIL else 'all passed'}")
sys.exit(1 if FAIL else 0)
