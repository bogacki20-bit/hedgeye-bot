"""Book staleness guard + conc_clusters sector-axis tests.

PURE — no DB, no network. The guard must be provable without a database: an
unreachable DB must never make a staleness check look green, which is the
failure mode the guard itself exists to prevent.

Run: python test_book_freshness.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.book_freshness import (STALE_AFTER_DAYS, age_days, is_stale,
                                  stale_note, status_line)
from tools.report import conc_clusters, conc_line, _CONC_TRAILING

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'OK' if ok else 'XX'} {label}: got {got!r}"
          + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


TODAY = date(2026, 8, 16)

# ── 1. age + threshold ──────────────────────────────────────────────────────
print("1. age and the 2-day threshold:")
check("threshold is 2 days", STALE_AFTER_DAYS, 2)
check("same day -> 0", age_days(date(2026, 8, 16), TODAY), 0)
check("9 days", age_days(date(2026, 8, 7), TODAY), 9)
check("None -> None", age_days(None, TODAY), None)
check("0d not stale", is_stale(date(2026, 8, 16), TODAY), False)
check("2d not stale (weekend)", is_stale(date(2026, 8, 14), TODAY), False)
check("3d IS stale", is_stale(date(2026, 8, 13), TODAY), True)
check("9d IS stale", is_stale(date(2026, 8, 7), TODAY), True)
check("UNKNOWN date is STALE, not fresh", is_stale(None, TODAY), True)

# ── 2. the note ─────────────────────────────────────────────────────────────
print("\n2. inline note:")
check("fresh -> None", stale_note(date(2026, 8, 15), TODAY), None)
check("2d -> None", stale_note(date(2026, 8, 14), TODAY), None)
check("9d -> note", stale_note(date(2026, 8, 7), TODAY),
      "book 9 days old (2026-08-07)")
check("None -> UNKNOWN note", stale_note(None, TODAY), "book date UNKNOWN")

# ── 3. status_line ALWAYS states the date ───────────────────────────────────
print("\n3. status_line is never silent:")
fresh = status_line(date(2026, 8, 15), TODAY)
stale = status_line(date(2026, 8, 7), TODAY)
unk = status_line(None, TODAY)
check("fresh states the date", "2026-08-15" in fresh, True)
check("fresh has no false alarm", "STALE" in fresh, False)
check("stale states the date", "2026-08-07" in stale, True)
check("stale shouts", stale.startswith("!! STALE BOOK"), True)
check("stale gives the day count", "9 days old" in stale, True)
check("stale says the figures are NOT today", "NOT today" in stale, True)
check("stale names the remedy", "_daily_upload.py" in stale, True)
check("unknown shouts too", unk.startswith("!! BOOK DATE UNKNOWN"), True)
check("every line is ASCII",
      all(ord(ch) < 128 for ch in fresh + stale + unk), True)
print("     fresh: %s" % fresh)
print("     stale: %s" % stale)

# ── 4. conc_clusters groups by whatever sector it is given ──────────────────
print("\n4. conc_clusters sector axis (now fed hedgeye_group):")
pm = [("SHEL", 10.0, ("ENERGY", 0, None, None, None, 0)),
      ("VLO", 8.0, ("ENERGY", 0, None, None, None, 0)),
      ("PM", 6.0, ("CONSUMER STAPLES", 0, None, None, None, 0)),
      ("VSH", 4.0, ("GLOBAL TECH", 0, None, None, None, 0)),
      ("BUXX", 12.0, (None, 0, None, None, None, 0)),   # row, NULL sector
      ("ZZZZ", 2.0, None)]                              # no ticker_tags row
c = conc_clusters(pm)
check("ENERGY -> energy", c["energy"], [2, 18.0])
check("CONSUMER STAPLES -> consumer_staples", c["consumer_staples"], [1, 6.0])
check("GLOBAL TECH -> global_tech", c["global_tech"], [1, 4.0])

# ── 5. unsectored names are BUCKETED, never dropped ─────────────────────────
print("\n5. unsectored names appear explicitly (never dropped):")
check("NULL sector -> no-sector bucket", c["no-sector"], [1, 12.0])
check("absent from ticker_tags -> no-tags", c["no-tags"], [1, 2.0])
check("old 'no-gics' label is gone", "no-gics" in c, False)
check("residuals are the trailing pair", sorted(_CONC_TRAILING),
      ["no-sector", "no-tags"])
total_pos = sum(n for n, _ in c.values())
check("every position landed in at least one bucket (6 in, >=6 counted)",
      total_pos >= 6, True)
weighted = sum(w for _, w in c.values())
check("no weight vanished (>= sum of inputs)", weighted >= 42.0, True)
line = conc_line(c)
check("BUXX's 12%% shows in the CONC line", "no-sector 1pos/12.0%w" in line, True)
print("     %s" % line)

# a book where EVERYTHING is unsectored must still report, not go blank
allnull = [("A", 50.0, (None, 0, None, None, None, 0)),
           ("B", 50.0, None)]
c2 = conc_clusters(allnull)
check("all-unsectored book still buckets", sorted(c2), ["no-sector", "no-tags"])
check("all-unsectored CONC line is not empty",
      conc_line(c2).startswith("CONC:"), True)

print("\n" + ("ALL PASS" if FAIL == 0 else f"{FAIL} FAILURE(S)"))
sys.exit(1 if FAIL else 0)
