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
