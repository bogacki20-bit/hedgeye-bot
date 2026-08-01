"""test_signal_strength_parse.py — the 2026-08-01 word-scrape fix.

Run:  python3 test_signal_strength_parse.py

parse_body used to lift English words out of email prose and store them as
tickers. HAS (Hasbro), JUST (a real GS ETF), BEEN, FROM and LIST reached the live
MFR enrollment backlog under a "paste into MFR -> Activate Assets" instruction.

Cause: the section regexes carried re.I, so their capture class [A-Z0-9,\\s] also
matched lowercase and ran on past the ticker list into the next sentence.

Pure logic. No DB, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser_signal_strength import (ADDED_RE, REMOVED_RE,   # noqa: E402
                                    STOPWORDS, parse_body)


def _p(txt):
    return parse_body(f"<p>{txt}</p>")


def test_the_documented_email_shape_still_parses():
    """From the module docstring: 'Added: WRBY, AS / Removed: CVX, BROS, JBHT'."""
    out = _p("Added: WRBY, AS Removed: CVX, BROS, JBHT")
    assert out["added"] == ["WRBY", "AS"]
    assert out["removed"] == ["CVX", "BROS", "JBHT"]


def test_prose_after_the_list_no_longer_leaks():
    """THE bug, reproduced verbatim."""
    out = _p("Added: AAPL NVDA coverage HAS BEEN expanded and the LIST is "
             "ranked JUST as before drawn FROM the universe Removed: TSLA")
    assert out["added"] == ["AAPL", "NVDA"], out["added"]
    for word in ("HAS", "BEEN", "LIST", "JUST", "FROM"):
        assert word not in out["added"], f"{word} leaked"


def test_real_adds_are_not_thrown_away_with_the_prose():
    """Case-sensitivity alone made the capture fail entirely, dropping the real
    tickers too — a silent bug traded for a loud one. The stop set must END the
    list at prose, not abandon the match."""
    out = _p("Added: AAPL NVDA and more coming Removed: TSLA")
    assert out["added"] == ["AAPL", "NVDA"], out["added"]
    assert out["removed"] == ["TSLA"]


def test_label_casing_is_still_tolerated():
    for label_a, label_r in (("Added", "Removed"), ("added", "removed"),
                             ("ADDED", "REMOVED")):
        out = _p(f"{label_a}: AAPL, NVDA {label_r}: TSLA")
        assert out["added"] == ["AAPL", "NVDA"], (label_a, out)
        assert out["removed"] == ["TSLA"], (label_r, out)


def test_multiline_and_bracketed_notes():
    assert _p("Added: AAPL,\nNVDA,\nMSFT\nRemoved: TSLA, F")["added"] == \
        ["AAPL", "NVDA", "MSFT"]
    assert _p("Added: AAPL, NVDA (see table) Removed: TSLA")["added"] == \
        ["AAPL", "NVDA"]


def test_removed_before_added():
    out = _p("Removed: CVX Added: WRBY")
    assert out["added"] == ["WRBY"] and out["removed"] == ["CVX"]


def test_stopwords_contain_no_real_ticker():
    """The previous patch for this bug was a four-word blocklist. A word list is
    the wrong tool here — HAS is Hasbro and JUST is the Goldman Sachs JUST US
    Large Cap Equity ETF. Anything added to STOPWORDS must not be a security."""
    for real in ("HAS", "JUST", "CAN", "ALL", "ONE", "NEW", "REAL", "OUT",
                 "GO", "AS", "EAT", "APP", "BUG", "R", "M", "T", "U", "V", "W"):
        assert real not in STOPWORDS, f"{real} is a listed security"


def test_capture_classes_are_case_sensitive():
    """re.I on the whole pattern is what caused this. The label may be
    case-insensitive; the ticker capture may not."""
    for rx in (ADDED_RE, REMOVED_RE):
        assert not (rx.flags & 2), f"{rx.pattern[:20]} must not carry re.I"


def test_no_ticker_survives_from_a_pure_prose_body():
    out = _p("Added: nothing today, the list is unchanged Removed: nothing")
    assert out["added"] == [] and out["removed"] == [], out


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
