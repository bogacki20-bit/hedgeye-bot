"""PM sector taxonomy tests — SCREEN's move from gics_sector to the Position
Monitor's own 15 sectors (ticker_tags.hedgeye_group).

PURE, per the repo's test doctrine: no DB, no network. Group 5 asserts the
taxonomy STRUCTURE of a captured roster fixture
(fixtures/pm_roster_2026-08-24.json) rather than live counts — 2026-08-25:
the live-DB version of group 5 baked the 8/17 roster and blocked a code merge
the day an authorized PM ingest moved the data. Data acceptance now lives in
_acceptance_live.py; this file tests logic only.

Run: python test_pm_sector_taxonomy.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.pm_parse import PM_SECTORS, pm_sector_key
from tools.screener import (parse_query, run_screen, _SECTORS, _SECTOR_NAMES,
                            _RETIRED_SECTORS, _assert_sector_order)

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'OK' if ok else 'XX'} {label}: got {got!r}"
          + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


PM15 = ["RESTAURANTS", "CONSUMER STAPLES", "CANNABIS", "GLL", "RETAIL",
        "HEALTHCARE", "FINANCIALS", "DIGITAL ASSETS", "SMALL CAPS",
        "INDUSTRIALS", "MATERIALS", "ENERGY", "SOFTWARE", "COMMUNICATIONS",
        "GLOBAL TECH"]

# ── 1. every PM sector name parses to exactly itself ────────────────────────
print("1. all 15 PM sector names parse to themselves:")
check("PM_SECTORS has 15", len(PM_SECTORS), 15)
check("_SECTOR_NAMES == the 15", sorted(_SECTOR_NAMES), sorted(PM15))
for name in PM15:
    q = parse_query(name.lower() + " longs")
    check(f"'{name.lower()} longs' -> {name}", q["sector"], name)
    check(f"'{name.lower()}' leaves nothing unrecognized", q["unrecognized"], [])

print("\n   the four multi-word names, and their spacing variants:")
for text, want in [("consumer staples longs", "CONSUMER STAPLES"),
                   ("digital assets longs", "DIGITAL ASSETS"),
                   ("small caps longs", "SMALL CAPS"),
                   ("small cap longs", "SMALL CAPS"),
                   ("smallcaps longs", "SMALL CAPS"),
                   ("global tech longs", "GLOBAL TECH"),
                   ("global technology longs", "GLOBAL TECH")]:
    q = parse_query(text)
    check(f"'{text}' -> {want}", q["sector"], want)
    check(f"'{text}' clean", q["unrecognized"], [])

# ── 2. GLOBAL TECH must NOT resolve to Technology ───────────────────────────
print("\n2. the GLOBAL TECH collision (\\btech\\b matching inside it):")
q = parse_query("global tech longs")
check("sector is GLOBAL TECH", q["sector"], "GLOBAL TECH")
check("sector is NOT Technology", q["sector"] == "Technology", False)
check("'global' is NOT left unrecognized", "global" in q["unrecognized"], False)
check("no retired_sector set", q.get("retired_sector"), None)
check("'Technology' is not a valid sector name at all",
      "Technology" in _SECTOR_NAMES, False)
# the structural guard itself
try:
    _assert_sector_order()
    check("_assert_sector_order passes", True, True)
except AssertionError as e:
    check(f"_assert_sector_order passes ({e})", False, True)
print("   same class of bug cannot hit the other multi-word names:")
for phrase, want in [("consumer staples", "CONSUMER STAPLES"),
                     ("digital assets", "DIGITAL ASSETS"),
                     ("small caps", "SMALL CAPS"),
                     ("gaming lodging leisure", "GLL")]:
    check(f"'{phrase}' whole-phrase -> {want}",
          parse_query(phrase + " longs")["sector"], want)

# ── 3. retired GICS names REFUSE, never return zero rows ────────────────────
print("\n3. retired GICS names refuse (assert on the refusal, not row count):")
for text, old in [("consumer discretionary longs", "Consumer Discretionary"),
                  ("discretionary longs", "Consumer Discretionary"),
                  ("technology longs", "Technology"),
                  ("tech longs", "Technology"),
                  ("information technology longs", "Technology"),
                  ("utilities longs", "Utilities"),
                  ("real estate longs", "Real Estate"),
                  ("reits longs", "Real Estate")]:
    q = parse_query(text)
    check(f"'{text}' flags retired={old}", q.get("retired_sector"), old)
    check(f"'{text}' sets NO sector", q["sector"], None)
    out = run_screen(text)
    check(f"'{text}' reply is a refusal", "is not a Hedgeye sector" in out, True)
    check(f"'{text}' does NOT render a zero-row result",
          "0 matches" in out or "match(es)" in out, False)
    check(f"'{text}' names the 15 valid sectors", "GLOBAL TECH" in out, True)

print("\n   GICS SPELLINGS of live PM sectors are aliases, NOT refusals:")
for text, want in [("health care longs", "HEALTHCARE"),
                   ("healthcare longs", "HEALTHCARE"),
                   ("communication services longs", "COMMUNICATIONS"),
                   ("communications longs", "COMMUNICATIONS")]:
    q = parse_query(text)
    check(f"'{text}' -> {want}", q["sector"], want)
    check(f"'{text}' not retired", q.get("retired_sector"), None)

# ── 4. GLL aliases ──────────────────────────────────────────────────────────
print("\n4. GLL aliases (acronym with no natural-language form):")
for text in ["gll longs", "gaming longs", "lodging longs", "leisure longs",
             "gaming lodging leisure longs", "gaming, lodging and leisure longs",
             "gaming lodging & leisure longs"]:
    q = parse_query(text)
    check(f"'{text}' -> GLL", q["sector"], "GLL")
    check(f"'{text}' clean", q["unrecognized"], [])

# ── 5. roster fixture STRUCTURE (not live counts, not a checksum) ───────────
# The fixture is a frozen capture with provenance. What is asserted here is
# that the taxonomy HOLDS over a real roster: every sector the ingest stored
# is one of the 15, none is empty, and the bucket partition sums to the total.
# Specific per-sector counts are deliberately NOT asserted — that is data
# acceptance, and it lives in _acceptance_live.py, off the merge gate.
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "pm_roster_2026-08-24.json")

print("\n5. roster fixture structure (fixtures/pm_roster_2026-08-24.json):")
with open(FIXTURE, encoding="utf-8") as f:
    fx = json.load(f)
check("fixture carries as_of", bool(fx.get("as_of")), True)
check("fixture carries source", bool(fx.get("source")), True)
sectors, bucket_counts = fx["sectors"], fx["buckets"]
check("every fixture sector is one of the 15",
      sorted(set(sectors) - PM_SECTORS), [])
check("all 15 sectors present", len(sectors), 15)
check("no sector is empty", sorted(s for s, n in sectors.items() if n <= 0), [])
check("sector counts sum to the total", sum(sectors.values()), fx["total"])
check("bucket counts sum to the total",
      sum(bucket_counts.values()), fx["total"])
check("all six buckets present", len(bucket_counts), 6)
check("bucket names are the canonical six",
      sorted(bucket_counts),
      ["active_long", "active_short", "long_bench", "short_bench",
       "top_idea_long", "top_idea_short"])
check("no bucket is empty",
      sorted(b for b, n in bucket_counts.items() if n <= 0), [])

# ── canonicalisation helper ────────────────────────────────────────────────
print("\n6. pm_sector_key canonicalisation:")
check("Title Case -> UPPER", pm_sector_key("Consumer Staples"), "CONSUMER STAPLES")
check("collapses inner whitespace", pm_sector_key(" global   tech "), "GLOBAL TECH")
check("None -> None", pm_sector_key(None), None)
check("blank -> None", pm_sector_key("   "), None)
check("every PM_SECTORS value is already canonical",
      sorted(x for x in PM_SECTORS if pm_sector_key(x) != x), [])

print("\n" + ("ALL PASS" if FAIL == 0 else f"{FAIL} FAILURE(S)"))
sys.exit(1 if FAIL else 0)
