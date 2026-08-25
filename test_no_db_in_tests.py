"""test_no_db_in_tests.py — the doctrine guard (A5, 2026-08-25).

Repo test doctrine: self-running scripts at the repo root, PURE logic, no DB,
no network. On 2026-08-24 two tests that asserted against live DB state
blocked a code merge the day an authorized ingest moved the data. This guard
is how that coupling does not grow back: it scans every test_*.py at the repo
root and fails on real DB coupling.

Real coupling =
  - calling db_pg._load_dotenv_fallback()
  - calling db_pg.get_conn()
  - importing db_pg at all

STUBBING db_pg (types.ModuleType("db_pg") planted in sys.modules, fake
get_conn attributes on the fake) is fine — that is how a pure test isolates
itself — and is deliberately not flagged.

Run: python test_no_db_in_tests.py
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Pre-existing offenders, found 2026-08-25 when this guard was written.
# Reported in RUN_REPORT_2026-08-25 and left alone per that brief — fixing
# them was out of scope. They are GRANDFATHERED, not endorsed: do not add to
# this set; shrink it by converting each file to fixtures.
#   test_trading_calendar.py — imports db_pg, loads dotenv, and calls
#       build_eod_pack() THREE times against the live DB + yfinance (it is
#       the source of the "mystery" pack artifacts of 2026-08-24 19:57 ET).
#   test_spotgamma.py — db_pg.get_conn() + a live SELECT on
#       spotgamma_snapshots.
GRANDFATHERED = {"test_trading_calendar.py", "test_spotgamma.py"}

VIOLATION_RES = [
    ("dotenv load", re.compile(r"_load_dotenv_fallback")),
    ("live connection", re.compile(r"db_pg\s*\.\s*get_conn")),
    ("db_pg import", re.compile(r"(?:^|\s)(?:import\s+db_pg\b|"
                                r"from\s+db_pg\s+import)")),
]

HERE = os.path.dirname(os.path.abspath(__file__))
ME = os.path.basename(__file__)

failures = []
grandfathered_hits = []
for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
    name = os.path.basename(path)
    if name == ME:
        continue
    with open(path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    hits = [label for label, rx in VIOLATION_RES if rx.search(src)]
    if not hits:
        continue
    if name in GRANDFATHERED:
        grandfathered_hits.append((name, hits))
    else:
        failures.append((name, hits))

for name, hits in grandfathered_hits:
    print(f"GRANDFATHERED  {name}: {', '.join(hits)} "
          f"(pre-existing, reported 2026-08-25 — do not extend)")

stale = sorted(n for n in GRANDFATHERED
               if not any(n == g for g, _ in grandfathered_hits))
for n in stale:
    print(f"STALE GRANDFATHER  {n}: no longer trips the scan — "
          f"remove it from GRANDFATHERED so it cannot regress silently")

if failures or stale:
    for name, hits in failures:
        print(f"VIOLATION  {name}: {', '.join(hits)} — tests are pure "
              f"logic; put data acceptance in _acceptance_live.py and "
              f"frozen inputs in fixtures/")
    print(f"\n{len(failures)} violation(s), {len(stale)} stale grandfather(s)")
    sys.exit(1)

print(f"\nOK — no live-DB coupling outside the {len(grandfathered_hits)} "
      f"grandfathered file(s)")
sys.exit(0)
