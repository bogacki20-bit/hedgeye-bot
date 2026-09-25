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


# ── 2026-09-25 dashboard layout: ADDING/REMOVING labels + full-roster table ──

_HDR = ["Ticker", "Name", "Sector", "Analyst", "Entry Date", "Entry Price",
        "Recent Price", "Total Return", "Best Idea Rank", "Holding Period"]


def _dash(adding, removing, rows):
    def tr(cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
    parts = ["<p>Signal Strength Stocks: 3 Stocks</p>",
             "<p>How to Use Signal Strength Stocks</p>"]
    if adding is not None:
        parts.append(f"<p><span>ADDING: </span><span>{adding}</span></p>")
    if removing is not None:
        parts.append(f"<p><span>REMOVING: </span><span>{removing}</span></p>")
    parts.append("<table>" + tr(_HDR) + "".join(tr(r) for r in rows) + "</table>")
    return "".join(parts)


_ROWS = [
    ["TXG", "10x Genomics Inc.", "Healthcare", "Tom Tobin", "05/19/26",
     "$21.70", "$82.59", "+277.47%", "3/6", "129 days"],
    ["MSFT", "Microsoft Corporation", "KM Signal", "KM Signal", "08/12/26",
     "$493.20", "$515.37", "+4.47%", "KM Signal", "44 days"],
    ["RPBPF", "Raspberry Pi Holdings Plc", "KM Signal", "KM Signal", "09/25/26",
     "$9.85", "$9.85", "-7.11%", "KM Signal", "0 days"],
]


def test_dashboard_adding_and_removing_labels():
    """The 9/25 email that parsed empty: '(8 added, 1 removed)'."""
    out = parse_body(_dash("AKAM, GLBE, GTBIF, LLY, MARA, MRVL, RBRK, RPBPF",
                           "U", _ROWS))
    assert out["added"][:2] == ["AKAM", "GLBE"], out["added"]
    assert "U" in out["removed"], out["removed"]


def test_dashboard_table_header_does_not_leak_a_T():
    """'Ticker' follows the REMOVING list directly; its capital T must not be
    harvested as AT&T."""
    raw = parse_body.__globals__["_strip_html"](_dash(None, "VREOF", _ROWS))
    m = REMOVED_RE.search(raw)
    assert m and m.group(1).split() == ["VREOF"], (m and m.group(1))


def test_dashboard_remove_only_email():
    out = _p("REMOVING: VREOF Ticker Name Sector")
    assert out["removed"] == ["VREOF"] and out["added"] == [], out


def test_roster_table_parses_every_row_and_skips_header():
    from parser_signal_strength import parse_roster_table
    rows = parse_roster_table(_dash("RPBPF", "U", _ROWS))
    assert [r["ticker"] for r in rows] == ["TXG", "MSFT", "RPBPF"], rows
    assert rows[1]["best_idea_rank"] == "KM Signal"
    assert rows[0]["entry_price"] == "$21.70"


def test_old_layout_has_no_roster():
    from parser_signal_strength import parse_roster_table
    assert parse_roster_table("<p>Added: WRBY Removed: CVX</p>") == []


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
