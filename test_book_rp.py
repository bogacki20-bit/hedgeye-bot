"""test_book_rp.py — BOOK RP / RP <TICKER> pure logic (PART E, 2026-08-24).

Run:  python test_book_rp.py

Pure logic only: no DB, no network. The IO paths (build_book_rp,
build_rp_single) are exercised by the merge gates against the live DB.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.report import (RP_ADD, RP_TRIM, _plausible_ticker,   # noqa: E402
                          format_book_rp, handle_report_command,
                          rp_summary, sort_rp_rows)
from tools.book_alerts import _book_rows                        # noqa: E402
from tools.mfr_coverage import is_dark_row, split_held          # noqa: E402


def _row(t, rp=None, side="long", acct="Individ", dark=False, **kw):
    d = {"ticker": t, "acct": acct, "side": side, "val": 1000.0, "pct": 4.0,
         "rp_now": rp, "rp_5d_min": 0.2 if rp is not None else None,
         "rp_5d_max": 0.9 if rp is not None else None,
         "trend": "BULLISH" if rp is not None else None,
         "bucket": "active_long", "dark": dark}
    d.update(kw)
    return d


# ─────────────────────── formatter: rp present and absent ───────────────────

def test_formatter_renders_rp_present_and_absent():
    body = format_book_rp([_row("JPM", 0.84), _row("ENZL", None, dark=True)])
    jpm = next(ln for ln in body.split("\n") if ln.startswith("JPM"))
    enzl = next(ln for ln in body.split("\n") if ln.startswith("ENZL"))
    assert "0.84" in jpm and "0.20-0.90" in jpm and "BULLISH" in jpm
    assert "n/a" in enzl and "DARK" in enzl
    assert "0.00" not in enzl, "an absent rp must never render as a number"


# ─────────────────────── the three summary buckets ──────────────────────────

def test_summary_boundaries_are_inclusive_at_080_and_020():
    rows = [_row("TRIM", 0.80), _row("ALMOST", 0.79),
            _row("ADD", 0.20), _row("NOTADD", 0.21),
            _row("MID", 0.50), _row("DK", None, dark=True)]
    s = rp_summary(rows)
    assert s["trim"] == ["TRIM"], s
    assert s["add"] == ["ADD"], s
    assert s["dark"] == ["DK"], s


def test_summary_is_longs_only():
    rows = [_row("SHRT", 0.95, side="short"), _row("SHLO", 0.05, side="short"),
            _row("LNG", 0.95)]
    s = rp_summary(rows)
    assert s["trim"] == ["LNG"] and s["add"] == []


def test_summary_dark_matches_the_shared_predicate():
    """BOOK RP's dark list and MFR COVERAGE's HELD AND DARK are the same
    predicate — assert the agreement on the same rows."""
    rows = [_row("JPM", 0.5), _row("ENZL", None, dark=True),
            _row("IAUI", None)]           # no flag, no rp -> still dark
    s = rp_summary(rows)
    dark, covered = split_held(rows)
    assert s["dark"] == dark == ["ENZL", "IAUI"]
    assert covered == ["JPM"]


# ─────────────────────── sorting: dark rows last ────────────────────────────

def test_dark_rows_sort_last_and_rp_descends():
    rows = [_row("DK", None, dark=True), _row("LO", 0.10), _row("HI", 0.90),
            _row("ND", None)]             # rp None without the flag = dark too
    ordered = [r["ticker"] for r in sort_rp_rows(rows)]
    assert ordered[:2] == ["HI", "LO"]
    assert set(ordered[2:]) == {"DK", "ND"}, "all dark rows must sort last"
    body = format_book_rp(rows)
    lines = [ln for ln in body.split("\n")
             if ln[:2] in ("DK", "LO", "HI", "ND")]
    assert lines[-1].startswith(("DK", "ND")) and lines[-2].startswith(("DK", "ND"))


# ─────────────────────── include_dark default ───────────────────────────────

def test_include_dark_defaults_to_false():
    """Every existing caller of _book_rows is an alert path and must not
    change behaviour — the parameter exists, keyword-only, default False."""
    sig = inspect.signature(_book_rows)
    p = sig.parameters["include_dark"]
    assert p.default is False
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


# ─────────────────────── RP <TICKER> declining ──────────────────────────────

def test_rp_declines_on_non_tickers():
    for bad in ("RP", "RP THE QUICK FOX", "RP 123456789012", "RP $$$",
                "RP lower case words", "RP 100.00"):
        assert handle_report_command(bad) is None, bad


def test_plausible_ticker_shapes():
    for good in ("AAPL", "BTC-USD", "BRK.B", "ES_F", "005930.KS"):
        assert _plausible_ticker(good), good
    for bad in ("", "2513", "TOOLONGNAME", "A B", "..", "100.00"):
        assert not _plausible_ticker(bad), bad


def test_boundaries_are_the_specced_constants():
    assert RP_TRIM == 0.80 and RP_ADD == 0.20


# ─────────────────────── runner ─────────────────────────────────────────────

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
        except Exception as e:
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
