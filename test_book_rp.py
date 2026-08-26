"""test_book_rp.py — BOOK RP v2 + rp_resolve pure logic (2026-08-26).

Run:  python test_book_rp.py

Pure logic only: no DB, no network. The IO paths are exercised by the merge
gates against the live DB.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.rp_resolve import (DIVERGENCE_THRESHOLD,           # noqa: E402
                              LOW_SIGNAL_BAND_PCT, divergence,
                              is_low_signal, resolve_rp, verdict, zone)
from tools.report import (_plausible_ticker, build_rp_clusters,  # noqa: E402
                          format_book_rp, format_rp_clusters,
                          handle_report_command, rp_zone_lists,
                          sort_rp_rows, top_corr_map)
from tools.book_alerts import _book_rows                        # noqa: E402
from tools.mfr_coverage import split_held                       # noqa: E402


def _row(t, rp=None, side="long", acct="Individ", dark=False, **kw):
    d = {"ticker": t, "acct": acct, "side": side, "val": 1000.0, "pct": 1.1,
         "rp_now": rp, "rp_lt": 0.5 if rp is not None else None,
         "rp_source": "mfr-published" if rp is not None else None,
         "rp_5d_min": 0.2 if rp is not None else None,
         "rp_5d_max": 0.9 if rp is not None else None,
         "trend": "BULLISH" if rp is not None else None,
         "bucket": "active_long", "dark": dark,
         "low_signal": False, "cash_eq": False}
    d.update(kw)
    return d


# ─────────────────── D3: the resolution order, four tiers ───────────────────

def test_resolution_order_never_silently_falls_through():
    assert resolve_rp(0.89, 1.29, "derived-hdg", 0.5, 0.4) == \
        (0.89, "mfr-published"), "published wins over everything"
    assert resolve_rp(None, 1.29, "derived-hdg", 0.5, 0.4) == \
        (1.29, "derived-hdg")
    assert resolve_rp(None, 0.72, None, 0.5, 0.4) == (0.72, "derived-mfr")
    assert resolve_rp(None, None, None, 0.5, 0.4) == (0.5, "shadow")
    assert resolve_rp(None, None, None, None, 0.4) == (0.4, "wrapper")
    assert resolve_rp() == (None, None), "nothing -> DARK, not a guess"
    # 0.0 is a real value at every tier, never treated as missing
    assert resolve_rp(0.0, 1.0) == (0.0, "mfr-published")
    assert resolve_rp(None, 0.0) == (0.0, "derived-mfr")


# ─────────────────── D4: the divergence alarm at > 0.05 ─────────────────────

def test_divergence_fires_beyond_the_threshold_only():
    assert DIVERGENCE_THRESHOLD == 0.05
    assert divergence(0.89, 1.29) is not None      # the HYG case
    assert abs(divergence(0.89, 1.29) - 0.40) < 1e-9
    assert divergence(0.50, 0.55) is None, "exactly 0.05 does not fire"
    assert divergence(0.50, 0.551) is not None
    assert divergence(None, 1.0) is None and divergence(1.0, None) is None


# ─────────────────── E2: five zones, exact boundaries, both sides ───────────

def test_zones_at_their_exact_boundaries():
    assert zone(1.01) == "BREAKOUT"
    assert zone(1.00) == "NEAR TOP"
    assert zone(0.80) == "NEAR TOP"
    assert zone(0.799) == "MID"
    assert zone(0.201) == "MID"
    assert zone(0.20) == "NEAR BOTTOM"
    assert zone(0.00) == "NEAR BOTTOM"
    assert zone(-0.001) == "BREAKDOWN"
    assert zone(None) is None


def test_verdicts_for_longs_and_shorts_invert():
    # longs: top = trim, bottom = add
    assert verdict("BREAKOUT", "long") == "trim"
    assert verdict("NEAR TOP", "long") == "trim"
    assert verdict("MID", "long") is None
    assert verdict("NEAR BOTTOM", "long") == "add"
    assert verdict("BREAKDOWN", "long") == "add"
    # shorts invert: top = add-to-short, bottom = cover. SUJA at 1.02 short
    # appeared nowhere under the old longs-only lines.
    assert verdict("BREAKOUT", "short") == "add"
    assert verdict("NEAR TOP", "short") == "add"
    assert verdict("MID", "short") is None
    assert verdict("NEAR BOTTOM", "short") == "cover"
    assert verdict("BREAKDOWN", "short") == "cover"
    assert verdict(None, "long") is None


def test_zone_lists_route_both_sides_and_respect_filters():
    rows = [_row("TRIML", 0.85), _row("ADDL", 0.10),
            _row("SHADD", 1.02, side="short"),
            _row("SHCOV", 0.05, side="short"),
            _row("MIDL", 0.50),
            _row("LOWSIG", 0.95, low_signal=True),
            _row("CASHEQ", 0.99, cash_eq=True),
            _row("DK", None, dark=True)]
    z = rp_zone_lists(rows)
    assert z["trim"] == ["TRIML"]
    # the ADD list carries BOTH a long at the bottom of its range and a
    # run-over short at the top of its — one list, two routes into it
    assert z["add"] == ["ADDL", "SHADD"], z["add"]
    assert z["cover"] == ["SHCOV"]
    assert z["low_signal"] == ["LOWSIG"], "low-signal excluded from verdicts"
    assert "CASHEQ" not in z["trim"], "cash-equivalent never a candidate"
    assert z["dark"] == ["DK"]


# ─────────────────── E3: the 2% low-signal filter ───────────────────────────

def test_low_signal_band_filter_at_two_percent():
    assert LOW_SIGNAL_BAND_PCT == 0.02
    # HYG-like: band 0.494 wide on a 79.92 price = 0.6% -> LOW-SIGNAL
    assert is_low_signal(79.478, 79.972, 79.92) is True
    # a 5%-wide band is a real signal
    assert is_low_signal(95.0, 100.0, 97.5) is False
    # exactly 2% is NOT low-signal (strictly under)
    assert is_low_signal(98.0, 100.0, 100.0) is False
    # unknowns are not low-signal — they are dark, a different statement
    assert is_low_signal(None, 100.0, 100.0) is False
    assert is_low_signal(98.0, 100.0, None) is False


# ─────────────────── E4: BOOK FULL and BOOK RP agree on cash-equivalents ────

def test_book_full_and_book_rp_share_the_cash_equivalent_source():
    """One classification, one source: both commands must read
    tools.position_targets.get_cash_equivalents — a BUXX that BOOK FULL
    parks as cash-equivalent must never be a BOOK RP trim candidate."""
    import tools.report as rpt
    src = inspect.getsource(rpt.build_book_rp)
    assert "get_cash_equivalents" in src
    import tools.position_targets as pt
    fsrc = inspect.getsource(pt)
    assert "def get_cash_equivalents" in fsrc
    # and the zone logic actually honours the flag
    z = rp_zone_lists([_row("BUXX", 0.87, cash_eq=True)])
    assert z["trim"] == [] and z["add"] == []


# ─────────────────── E6/E7: clusters from a fixture matrix ──────────────────

FIXTURE_PAIRS = [
    {"a": "CFG", "b": "JPM", "corr": 0.91},
    {"a": "JPM", "b": "WFC", "corr": 0.85},
    {"a": "MA", "b": "V", "corr": 0.88},
    {"a": "CFG", "b": "MA", "corr": 0.72},
    {"a": "SNOW", "b": "JPM", "corr": 0.10},   # weak — no union
    {"a": "GLD", "b": "JPM", "corr": -0.20},
]
FIXTURE_DOLLARS = {"CFG": 700.0, "JPM": 700.0, "WFC": 800.0, "MA": 650.0,
                   "V": 900.0, "SNOW": 880.0, "GLD": 500.0, "IAUI": 460.0}


def test_cluster_assembly_from_fixture_matrix():
    cl = build_rp_clusters(FIXTURE_PAIRS, FIXTURE_DOLLARS, 100_000.0)
    assert len(cl["clusters"]) == 1, cl["clusters"]
    c = cl["clusters"][0]
    assert c["members"] == ["CFG", "JPM", "MA", "V", "WFC"], \
        "0.72 CFG-MA bridges banks and payments into ONE bet"
    assert abs(c["dollars"] - 3750.0) < 1e-9
    assert abs(c["pct"] - 3.75) < 1e-9
    assert c["max_corr"] == 0.91
    assert c["avg_corr"] is not None and 0.7 < c["avg_corr"] < 0.92


def test_clusters_flag_the_cap_thresholds():
    cl = build_rp_clusters(FIXTURE_PAIRS, FIXTURE_DOLLARS, 25_000.0)
    lines = "\n".join(format_rp_clusters(cl))
    assert "REJECT-LEVEL" in lines, \
        "a 15% cluster must be flagged at the sector cap's 12% threshold"


def test_unclustered_names_are_reported_not_dropped():
    cl = build_rp_clusters(FIXTURE_PAIRS, FIXTURE_DOLLARS, 100_000.0)
    assert cl["unclustered"] == ["IAUI"], \
        "a name with no pair rows must land in UNCLUSTERED"
    assert abs(cl["unclustered_dollars"] - 460.0) < 1e-9
    lines = "\n".join(format_rp_clusters(cl))
    assert "UNCLUSTERED" in lines and "IAUI" in lines
    # SNOW and GLD have pair data (weak) — correlated-but-alone, not dropped
    assert "SNOW" not in cl["unclustered"]


def test_top_corr_picks_the_strongest_by_absolute_value():
    tc = top_corr_map(FIXTURE_PAIRS, list(FIXTURE_DOLLARS))
    assert tc["CFG"] == ("JPM", 0.91)
    assert tc["GLD"] == ("JPM", -0.20), "negative corr counts by |value|"
    assert "IAUI" not in tc, "no data renders n/a, never 0.00"


# ─────────────────── formatter + sorting ────────────────────────────────────

def test_formatter_renders_both_ranges_src_and_tags():
    body = format_book_rp([_row("JPM", 0.84), _row("ENZL", None, dark=True),
                           _row("HYG", 0.89, low_signal=True)],
                          total_book=93_080.89)
    jpm = next(ln for ln in body.split("\n") if ln.startswith("JPM"))
    enzl = next(ln for ln in body.split("\n") if ln.startswith("ENZL"))
    hyg = next(ln for ln in body.split("\n") if ln.startswith("HYG"))
    assert "0.84" in jpm and "0.50" in jpm and "mfr" in jpm
    assert "n/a" in enzl and "DARK" in enzl
    assert "LOW-SIGNAL" in hyg
    assert "93,080.89" in body, "%book denominator is stated"


def test_dark_rows_sort_last_and_rp_descends():
    rows = [_row("DK", None, dark=True), _row("LO", 0.10), _row("HI", 0.90),
            _row("ND", None)]
    ordered = [r["ticker"] for r in sort_rp_rows(rows)]
    assert ordered[:2] == ["HI", "LO"]
    assert set(ordered[2:]) == {"DK", "ND"}


def test_dark_agreement_with_coverage_predicate():
    rows = [_row("JPM", 0.5), _row("ENZL", None, dark=True),
            _row("IAUI", None)]
    dark, covered = split_held([dict(r, rp_now=r["rp_now"]) for r in rows])
    assert dark == ["ENZL", "IAUI"] and covered == ["JPM"]


# ─────────────────── include_dark + RP <TICKER> gates ───────────────────────

def test_include_dark_defaults_to_false():
    sig = inspect.signature(_book_rows)
    p = sig.parameters["include_dark"]
    assert p.default is False
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_rp_declines_on_non_tickers():
    for bad in ("RP", "RP THE QUICK FOX", "RP 123456789012", "RP $$$",
                "RP lower case words", "RP 100.00"):
        assert handle_report_command(bad) is None, bad


def test_plausible_ticker_shapes():
    for good in ("AAPL", "BTC-USD", "BRK.B", "ES_F", "005930.KS"):
        assert _plausible_ticker(good), good
    for bad in ("", "2513", "TOOLONGNAME", "A B", "..", "100.00"):
        assert not _plausible_ticker(bad), bad


# ─────────────────── runner ─────────────────────────────────────────────────

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
