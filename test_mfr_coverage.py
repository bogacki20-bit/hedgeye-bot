"""test_mfr_coverage.py — MFR COVERAGE pure logic (PART C, 2026-08-24).

Run:  python test_mfr_coverage.py

History: the operator reported "the backlog keeps giving me the same list
twice". The 8/18 and 8/24 diagnostics both found the backlog CORRECT — the
real gaps were (a) no way to see WANTED / ENROLLED / SERVED as separate sets
and (b) nothing storing yesterday's backlog, making "same as yesterday"
unfalsifiable. MFR COVERAGE + mfr_backlog_snapshots (078) fix both.

Pure logic only: no DB, no network, no MFR calls.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.mfr_coverage import (classify_universe, delta_line,   # noqa: E402
                                format_report, split_held)


# ─────────────────────── three-way classification ───────────────────────

def test_classification_buckets_are_disjoint_and_complete():
    wanted = {"AAPL", "JPM", "DARKCO", "NEWCO", "BTCUSD"}
    enrolled = {"AAPL", "JPM", "DARKCO"}
    served = {"AAPL", "JPM"}
    parked = {"BTCUSD"}
    c = classify_universe(wanted, enrolled, served, parked)
    assert c["served"] == {"AAPL", "JPM"}
    assert c["enrolled_dark"] == {"DARKCO"}
    assert c["not_enrolled"] == {"NEWCO"}
    assert c["parked"] == {"BTCUSD"}
    union = c["served"] | c["enrolled_dark"] | c["not_enrolled"] | c["parked"]
    assert union == wanted, "every wanted name lands in exactly one bucket"


def test_parked_wins_over_every_other_bucket():
    """A deliberately-excluded name must never read as missing — even if it
    happens to be enrolled or served under some alias accident."""
    c = classify_universe({"BTCUSD"}, {"BTCUSD"}, {"BTCUSD"}, {"BTCUSD"})
    assert c["parked"] == {"BTCUSD"}
    assert not c["served"] and not c["enrolled_dark"] and not c["not_enrolled"]


def test_served_but_not_enrolled_still_counts_as_served():
    """The alias case (BTC served while BTCUSD is what's wanted): if the
    wanted symbol itself shows in served, it is covered, enrolled or not."""
    c = classify_universe({"XLE"}, set(), {"XLE"}, set())
    assert c["served"] == {"XLE"}


# ─────────────────────────── held-and-dark ───────────────────────────

ROWS = [
    {"ticker": "JPM", "side": "long", "rp_now": 0.71},
    {"ticker": "ENZL", "side": "long", "rp_now": None, "dark": True},
    {"ticker": "KR", "side": "short", "rp_now": 0.55},
    {"ticker": "BUXX", "side": "long", "rp_now": None, "dark": True},
]


def test_split_held_separates_dark_from_covered_sorted():
    dark, covered = split_held(ROWS)
    assert dark == ["BUXX", "ENZL"]
    assert covered == ["JPM", "KR"]


def test_split_held_empty_book():
    assert split_held([]) == ([], [])


def test_a_row_with_no_rp_is_dark_even_without_the_flag():
    """The slice LEFT-JOINs from the member list, so a held name with no
    range still gets a row — rp_now None IS darkness, flag or no flag."""
    dark, covered = split_held([{"ticker": "IAUI", "rp_now": None},
                                {"ticker": "JPM", "rp_now": 0.5}])
    assert dark == ["IAUI"] and covered == ["JPM"]


def test_held_and_dark_prints_first_in_the_report():
    body = format_report(asof=date(2026, 8, 24),
                         universe={"JPM", "ENZL", "KR", "BUXX"},
                         enrolled={"JPM", "KR"}, served={"JPM", "KR"},
                         parked=set(), backlog=["ENZL", "BUXX"],
                         backlog_note="", held_rows=ROWS, dark_days={},
                         prior=None)
    lines = body.split("\n")
    idx_dark = next(i for i, l in enumerate(lines)
                    if l.startswith("HELD AND DARK"))
    idx_served = next(i for i, l in enumerate(lines)
                      if l.startswith("SERVED"))
    assert idx_dark < idx_served, "held-and-dark must lead the report"
    assert "BUXX ENZL" in lines[idx_dark + 1]


# ─────────────────────────── the delta line ───────────────────────────

def test_delta_line_against_stored_prior_day():
    line = delta_line(date(2026, 8, 24), ["JKL", "XYZ"],
                      (date(2026, 8, 23), {"ABC", "DEF", "XYZ"}))
    assert "vs 2026-08-23" in line
    assert "backlog 3 -> 2" in line
    assert "cleared: ABC DEF" in line
    assert "new: JKL" in line


def test_delta_line_unchanged_backlog_says_so_plainly():
    """The operator's exact complaint: the same list two days running must
    render as an explicit no-change, not silence."""
    line = delta_line(date(2026, 8, 24), ["ABC", "DEF"],
                      (date(2026, 8, 23), {"ABC", "DEF"}))
    assert "backlog 2 -> 2" in line
    assert "cleared: none" in line and "new: none" in line


def test_delta_line_first_run_has_no_prior():
    line = delta_line(date(2026, 8, 24), ["ABC"], None)
    assert "no prior" in line.lower()
    assert "078" in line


# ─────────────────────── SERVED empty = stated failure ───────────────────────

def test_empty_served_renders_as_feed_failure_not_universal_darkness():
    body = format_report(asof=date(2026, 8, 24),
                         universe={"AAPL", "JPM"}, enrolled={"AAPL", "JPM"},
                         served=set(), parked=set(), backlog=[],
                         backlog_note="", held_rows=[], dark_days={},
                         prior=None)
    assert "SERVED: EMPTY" in body
    assert "feed" in body, "must name the feed as the suspect"
    # the failure line must replace the served list, not coexist with a
    # confident 'SERVED (0)' that reads as an empty-but-healthy account
    assert "SERVED (0)" not in body


def test_report_headline_counts():
    body = format_report(asof=date(2026, 8, 24),
                         universe={"A", "B", "C"}, enrolled={"A", "B"},
                         served={"A"}, parked=set(), backlog=["C"],
                         backlog_note="", held_rows=[], dark_days={"B": 4},
                         prior=None)
    assert "universe 3 | enrolled 2 | served 1" in body
    assert "dark 1" in body
    assert "B        last snapshot 4d ago" in body
    assert "NOT-ENROLLED (1)" in body


# ─────────────────────────── runner ───────────────────────────

if __name__ == "__main__":
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
