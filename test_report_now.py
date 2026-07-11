"""Fixture tests for REPORT NOW pure logic (no DB, no network) — rotation
cues, live rp, candidate selection, sizing flags, formatting.
    python test_report_now.py
"""
from tools.report_now import (rotation_cues, live_rp, pick_candidates,
                              sizing_flags, fmt_candidate, fmt_section)


# ── rotation cues ──

FLOWS = [("XLB", 0.48, +0.38, "✗"), ("XLY", 0.54, +0.23, "✓"),
         ("XLK", 0.52, +0.03, "✓"), ("XLF", 0.43, -0.08, "✗"),
         ("XLE", 0.61, -0.25, "✗"), ("XLV", 0.22, -0.31, ""),
         ("XLRE", 0.42, None, "")]


def test_rotation_cues_threshold_and_order():
    hot, cool = rotation_cues(FLOWS, delta=0.10)
    assert [s for s, _, _ in hot] == ["XLB", "XLY"]      # |Δ| desc
    assert [s for s, _, _ in cool] == ["XLV", "XLE"]     # most-out first
    # small moves and None-Δ never cue
    assert "XLK" not in [s for s, _, _ in hot]
    assert "XLF" not in [s for s, _, _ in cool]
    assert "XLRE" not in [s for s, _, _ in hot + cool]


def test_rotation_cues_keep_flow_quality_mark():
    hot, _ = rotation_cues(FLOWS)
    assert hot[0] == ("XLB", 0.38, "✗")                  # fade cue stays marked


# ── live rp ──

def test_live_rp_basic_and_unclamped():
    assert live_rp(105, 100, 110) == 0.5
    assert live_rp(112, 100, 110) == 1.2                 # through the top: real info
    assert abs(live_rp(98, 100, 110) - (-0.2)) < 1e-9    # through the bottom


def test_live_rp_undefined_is_none_never_guessed():
    assert live_rp(None, 100, 110) is None
    assert live_rp(105, None, 110) is None
    assert live_rp(105, 110, 110) is None                # degenerate range


# ── candidate selection ──

def row(t, sec, td, rp):
    return {"ticker": t, "sector": sec, "trend_dir": td, "rp_live": rp}


def test_pick_candidates_directional_routing():
    hot, cool = rotation_cues(FLOWS)
    rows = [row("BYD", "Consumer Discretionary", "BULLISH", 0.14),
            row("RH", "Consumer Discretionary", "BULLISH", 0.31),
            row("HIGH", "Consumer Discretionary", "BULLISH", 0.60),   # rp too high
            row("BEAR", "Consumer Discretionary", "BEARISH", 0.10),   # wrong trend
            row("UHS", "Health Care", "BEARISH", 0.81),
            row("LOWS", "Health Care", "BEARISH", 0.40),              # rp too low
            row("XOM", "Energy", "BEARISH", 0.90),
            row("NOPE", "Technology", "BULLISH", 0.05)]               # sector not cued
    # sector labels in cues are ETF symbols; map rows to the cue keys used
    hot = [("Consumer Discretionary", 0.23, "✓")]
    cool = [("Health Care", -0.31, ""), ("Energy", -0.25, "✗")]
    longs, shorts = pick_candidates(rows, hot, cool)
    assert [r["ticker"] for r in longs] == ["BYD", "RH"]              # rp asc
    assert [r["ticker"] for r in shorts] == ["XOM", "UHS"]            # rp desc


def test_pick_candidates_none_rp_excluded():
    longs, _ = pick_candidates([row("X", "S", "BULLISH", None)],
                               [("S", 0.2, "")], [])
    assert longs == []


# ── sizing flags (flag, never hide) ──

def test_sizing_flags_short_hard_cap():
    assert sizing_flags("short", 2.5, is_fund=False) == "⚠HARD2"
    assert sizing_flags("short", 1.9, is_fund=False) == ""


def test_sizing_flags_etf_cap_and_ceiling():
    assert sizing_flags("long", 4.5, is_fund=True) == "⚠cap4"
    assert sizing_flags("long", 6.5, is_fund=True) == "⚠ceil6"
    assert sizing_flags("long", 4.5, is_fund=False) == ""   # single name: no ETF cap
    assert sizing_flags("long", None, is_fund=True) == ""   # unknown: no flag


# ── formatting ──

def test_fmt_candidate_unheld_and_held():
    r = row("BYD", "CD", "BULLISH", 0.14)
    assert fmt_candidate(r, None) == "BYD 0.14"
    ctx = {"fill": 71.0, "acct": "IND", "acct_pct": 1.4, "tgt_src": "dflt-eq"}
    assert fmt_candidate(row("RH", "CD", "BULLISH", 0.31), ctx) == \
        "RH 0.31 held71%,IND"


def test_fmt_candidate_breach_still_surfaces_with_flag():
    ctx = {"fill": 134.0, "acct": "IND+ROTH", "acct_pct": 5.4,
           "tgt_src": "dflt-core"}
    s = fmt_candidate(row("CLOX", "F", "BULLISH", 0.20), ctx)
    assert s == "CLOX 0.20 held134%,IND+ROTH⚠cap4"


def test_fmt_section_cap_and_empty():
    assert fmt_section("LONGS", [], {}) == "LONGS: none"
    rows = [row(f"T{i}", "S", "BULLISH", 0.10 + i / 100) for i in range(12)]
    s = fmt_section("LONGS", rows, {}, cap=10)
    assert "+2 more" in s and s.count("·") == 9


if __name__ == "__main__":
    import sys, inspect
    fails = 0
    for name, fn in sorted(inspect.getmembers(sys.modules["__main__"],
                                              inspect.isfunction)):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
