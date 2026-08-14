"""test_side_blocks.py — the 2026-08-02 keiths_signals word-scrape fix.

Run:  python3 test_side_blocks.py

hedgeye_ticker_history proved the origin: BEEN FROM HAS JUST LIST SIGNAL, all
source=keiths_signals, all six within a two-second window on 2026-07-29, each
seen exactly once in 23,959 rows. One parse of one email, not accumulated drift.

parser_keiths_signals -> parser_research_common.side_blocks -> _SIDE_BLOCK_RE,
whose segment was `.+?` under re.S: it ran across newlines until it found a
terminator, feeding up to 600 characters of prose to a 1-6 char token matcher.
That 6 is why SIGNAL appeared here and could not have come from
parser_signal_strength, which caps at 5.

Pure logic. No DB, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser_research_common import (STOP, TICKER_TOK_RE,      # noqa: E402
                                    _SIDE_BLOCK_RE, side_blocks)


def _t(txt):
    return {(r["ticker"], r["side"]) for r in side_blocks(txt)}


def test_clean_pair():
    assert _t("LONGS: AAPL, NVDA SHORTS: TSLA, F") == {
        ("AAPL", "long"), ("NVDA", "long"), ("TSLA", "short"), ("F", "short")}


def test_label_casing_still_matches():
    for lo, sh in (("LONGS", "SHORTS"), ("Longs", "Shorts"),
                   ("BULLISH", "BEARISH")):
        got = _t(f"{lo}: AAPL {sh}: TSLA")
        assert got == {("AAPL", "long"), ("TSLA", "short")}, (lo, got)


def test_prose_after_the_list_no_longer_leaks():
    """THE bug, with every word that actually reached production."""
    got = _t("LONGS: AAPL, NVDA and coverage HAS BEEN expanded so the LIST is "
             "JUST a SIGNAL drawn FROM everything SHORTS: TSLA")
    assert ("AAPL", "long") in got and ("NVDA", "long") in got
    assert ("TSLA", "short") in got
    for w in ("HAS", "BEEN", "LIST", "JUST", "SIGNAL", "FROM"):
        assert w not in {t for t, _ in got}, f"{w} leaked"


def test_real_tickers_survive_the_bound():
    """Bounding must END the list, not abandon it — dropping Keith's real
    longs would be a silent bug in place of a loud one."""
    got = _t("LONGS: AAPL, NVDA more to follow SHORTS: TSLA")
    assert ("AAPL", "long") in got and ("NVDA", "long") in got


def test_newlines_inside_the_list_are_fine():
    assert _t("LONGS:\nAAPL,\nNVDA\n\nSHORTS:\nTSLA") == {
        ("AAPL", "long"), ("NVDA", "long"), ("TSLA", "short")}


def test_existing_tail_terminators_still_work():
    assert _t("LONGS: AAPL SHORTS: TSLA Please visit the site") == {
        ("AAPL", "long"), ("TSLA", "short")}


def test_pattern_no_longer_carries_global_ignorecase():
    """re.I would make the [a-z] bound useless — it matches uppercase too."""
    assert not (_SIDE_BLOCK_RE.flags & 2), "re.I must not be global here"


def test_six_char_tokens_are_why_SIGNAL_landed_here():
    assert TICKER_TOK_RE.fullmatch("SIGNAL"), \
        "TICKER_TOK_RE allows 1-6 chars; that is the SIGNAL path"


def test_stop_list_still_shadows_real_tickers():
    """NOT a fix — a standing flag. KEY (KeyCorp), AI (C3.ai), GOLD (Barrick)
    and OIL are tradeable symbols sitting in STOP, so if Keith goes long any of
    them the parser silently drops it. They are ALSO generic words in this
    prose, so removing them is an operator call, not a code call. This test
    exists so the tradeoff cannot be forgotten."""
    shadowed = sorted({"KEY", "AI", "GOLD", "OIL"} & STOP)
    assert shadowed == ["AI", "GOLD", "KEY", "OIL"], shadowed


# ─────────────────────────────────────────────────────────────────────
# 2026-08-14 — the SECOND defect from the same 7/29 intra-week email: a
# compound "Longs/Shorts:" header whose "Shorts:" swallowed the whole LONGS
# block, mis-storing every long as short for ~2.5 weeks. Tests A4.1–A4.5.

_HEALTHY_727 = (  # real 2026-07-27 body shape — the format that always worked
    "Keith's Signal Strength List LONGS: V, MA, XYZ, FICO, COF, CFG, CPAY, "
    "GPN, PYPL, OMF, TRU, EXPN, SPGI, JPM, WFC, COMP SHORTS : ADYEY, FISV, "
    "AXP, AFRM, SYF, SOFI, ALLY, OPEN, RKT, ZG, FCFS")

_BROKEN_729 = (  # real 2026-07-29 body shape — compound "Longs/Shorts:" header
    "Keith's updated Signal Strength Longs/Shorts: LONGS: V, MA, XYZ, FICO, "
    "COF, CFG, CPAY, GPN, PYPL, OMF, TRU, EXPN, SPGI, JPM, WFC, COMP "
    "SHORTS : ADYEY, AXP, AFRM, SYF, SOFI, ALLY, OPEN, RKT, ZG, FCFS")


def test_compound_header_no_longer_swallows_the_longs_block():
    """THE 2026-07-29 bug: the 'Shorts:' in a 'Longs/Shorts:' header must NOT
    consume the following LONGS block. Longs must survive as LONG."""
    got = _t(_BROKEN_729)
    shorts = {t for t, s in got if s == "short"}
    for t in ("V", "MA", "JPM", "WFC", "XYZ", "COMP"):
        assert (t, "long") in got, f"{t} should be LONG, got={sorted(got)}"
        assert t not in shorts, f"{t} leaked to SHORT"
    assert {t for t, s in got if s == "long"}, "LONGS block was swallowed"
    assert ("ADYEY", "short") in got and ("AXP", "short") in got


def test_healthy_pre_0727_format_unchanged():
    """A4.3 — the pre-07-27 format still parses EXACTLY as before."""
    got = _t(_HEALTHY_727)
    longs = sorted(t for t, s in got if s == "long")
    shorts = sorted(t for t, s in got if s == "short")
    assert longs == ["CFG", "COF", "COMP", "CPAY", "EXPN", "FICO", "GPN", "JPM",
                     "MA", "OMF", "PYPL", "SPGI", "TRU", "V", "WFC", "XYZ"], longs
    assert shorts == ["ADYEY", "AFRM", "ALLY", "AXP", "FCFS", "FISV", "OPEN",
                      "RKT", "SOFI", "SYF", "ZG"], shorts


def test_shorts_first_two_uppercase_blocks():
    """A4.1 — SHORTS block immediately followed by LONGS block, no lowercase."""
    assert _t("SHORTS: TSLA, F LONGS: AAPL, NVDA") == {
        ("TSLA", "short"), ("F", "short"), ("AAPL", "long"), ("NVDA", "long")}


def test_longs_first_two_uppercase_blocks():
    """A4.2 — LONGS block first, SHORTS after, all uppercase."""
    assert _t("LONGS: AAPL, NVDA SHORTS: TSLA, F") == {
        ("AAPL", "long"), ("NVDA", "long"), ("TSLA", "short"), ("F", "short")}


def test_label_adjacent_blocks_do_not_cross_contaminate():
    """A block whose label is immediately followed by the next label (empty
    ticker list) must not swallow that next block — the case the plain
    lookahead could not catch because seg began on the next label's letter."""
    got = _t("SHORTS: LONGS: AAPL, NVDA")
    assert ("AAPL", "long") in got and ("NVDA", "long") in got
    assert "AAPL" not in {t for t, s in got if s == "short"}


def test_slash_compound_label_is_not_a_block():
    """(?<!/): a side label glued to a slash ('Longs/Shorts:') is a header,
    not a block, so it mints no tickers and swallows nothing."""
    got = _t("Signal Longs/Shorts: LONGS: AAPL SHORTS: TSLA")
    assert got == {("AAPL", "long"), ("TSLA", "short")}, sorted(got)


def test_paren_only_consumer_path_still_works():
    """A4.4 — parser_macro_show calls side_blocks(paren_only=True). Confirm the
    paren-only extraction is unaffected by the fix."""
    rows = side_blocks("LONGS: (AAPL) plain NVDA SHORTS: (TSLA)",
                       paren_only=True)
    got = {(r["ticker"], r["side"]) for r in rows}
    assert ("AAPL", "long") in got and ("TSLA", "short") in got
    assert "NVDA" not in {t for t, _ in got}, "paren_only must skip bare NVDA"


def test_ingest_one_sided_guard_decision():
    """A4.5 — pure guard: refuse a one-sided load only when the prior load had
    both sides."""
    from parser_keiths_signals import one_sided_refusal
    assert one_sided_refusal({"short"}, {"long", "short"}) is True   # 7/29 case
    assert one_sided_refusal({"long"}, {"long", "short"}) is True
    assert one_sided_refusal({"long", "short"}, {"long", "short"}) is False
    assert one_sided_refusal({"short"}, {"short"}) is False          # prev 1-sided
    assert one_sided_refusal({"short"}, set()) is False              # no prior load


if __name__ == "__main__":
    import inspect
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
            except Exception as e:
                fails += 1
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
