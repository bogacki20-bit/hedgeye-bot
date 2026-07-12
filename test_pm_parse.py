"""Fixture tests for tools/pm_parse (no DB, no network). The fixture is a
condensed slice of the real 7/6/26 PDF's pypdf text, keeping every layout
edge case: leading-space and bare headers, wrapped name lines, ALL-CAPS
company names, self-named crypto tickers, international symbols, an empty
bucket directly followed by a sector, and a sector without leading space.
    python test_pm_parse.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.pm_parse import diff_summary, parse_position_monitor

FIXTURE = """HEDGEYE POSITION MONITOR (07/06/2026)
 RESTAURANTS
 ACTIVE LONGS
BROS
Dutch Bros Inc.
 TOP IDEA SHORTS
CMG
Chipotle Mexican Grill, Inc.
 LONG BENCH
BRCB
Black Rock Coffee Bar, Inc. Class A Common
Stock
 CONSUMER STAPLES
 ACTIVE SHORTS
LISN.SW
Chocoladefabriken Lindt & Sprüngli AG
BF-B
Brown-Forman Corporation
SFD
SMITHFIELD FOODS INC
 SHORT BENCH

DIGITAL ASSETS
 ACTIVE SHORTS
AVAXUSD
AVAXUSD
BTCUSD
Bitcoin
LONG BENCH
RUNEUSD
THORChain
GLOBAL TECH
 ACTIVE LONGS
005930.KS
Samsung Electronics Co., Ltd.
VOLV-B.ST
Volvo AB
GLL
 ACTIVE LONGS
VIK
Viking Holdings Ltd
"""

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'✅' if ok else '🛑'} {label}: got {got!r}"
          + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


p = parse_position_monitor(FIXTURE)

print("masthead:")
check("report_date", p["report_date"], date(2026, 7, 6))

print("buckets:")
check("BROS active_long", p["mapping"].get("BROS"), "active_long")
check("CMG top_idea_short", p["mapping"].get("CMG"), "top_idea_short")
check("BRCB long_bench", p["mapping"].get("BRCB"), "long_bench")
check("LISN.SW active_short (intl dot)", p["mapping"].get("LISN.SW"),
      "active_short")
check("BF-B active_short (hyphen)", p["mapping"].get("BF-B"), "active_short")
check("SFD active_short (caps name not eaten)", p["mapping"].get("SFD"),
      "active_short")
check("AVAXUSD active_short (self-named)", p["mapping"].get("AVAXUSD"),
      "active_short")
check("BTCUSD active_short", p["mapping"].get("BTCUSD"), "active_short")
check("RUNEUSD long_bench (bare header)", p["mapping"].get("RUNEUSD"),
      "long_bench")
check("005930.KS active_long (numeric intl)", p["mapping"].get("005930.KS"),
      "active_long")
check("VOLV-B.ST active_long (two separators)",
      p["mapping"].get("VOLV-B.ST"), "active_long")
check("GLL is a sector, not a ticker (lookahead)",
      "GLL" in p["mapping"], False)
check("VIK active_long under GLL", p["mapping"].get("VIK"), "active_long")
check("VIK sector stays acronym", p["sectors"].get("VIK"), "GLL")
check("roster size", len(p["mapping"]), 12)

print("sectors / names:")
check("BROS sector", p["sectors"].get("BROS"), "Restaurants")
check("BTCUSD sector (bare sector header)", p["sectors"].get("BTCUSD"),
      "Digital Assets")
check("005930.KS sector", p["sectors"].get("005930.KS"), "Global Tech")
check("BRCB wrapped name", p["names"].get("BRCB"),
      "Black Rock Coffee Bar, Inc. Class A Common Stock")
check("SFD caps name", p["names"].get("SFD"), "SMITHFIELD FOODS INC")

print("warnings:")
check("no warnings on clean fixture", p["warnings"], [])

print("diff_summary:")
trans = [
    {"ticker": "NEW1", "from": None, "to": "active_long"},
    {"ticker": "MOV1", "from": "long_bench", "to": "active_long"},
    {"ticker": "GONE", "from": "active_short", "to": "removed"},
]
s = diff_summary(trans, 11, date(2026, 7, 6))
check("header", s.splitlines()[0],
      "PM 2026-07-06 · roster 11 · 3 changes")
check("NEW line", "NEW: NEW1(active_long)" in s, True)
check("MOVED line", "MOVED: MOV1 long_bench→active_long" in s, True)
check("REMOVED line", "REMOVED: GONE" in s, True)
s0 = diff_summary([], 11, date(2026, 7, 6), removals_skipped=True)
check("no-change + guard note",
      "no bucket changes" in s0 and "removals NOT checked" in s0, True)

undated = parse_position_monitor("no masthead here\nAAPL\nApple Inc.")
check("undated is loud", any("UNDATED" in w for w in undated["warnings"]),
      True)

print(f"\n{'🛑 ' + str(FAIL) + ' FAILURES' if FAIL else '✅ all passed'}")
sys.exit(1 if FAIL else 0)
