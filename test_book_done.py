"""test_book_done.py — BOOK RP's DONE block (Kris 9/29: it kept telling him to
cover names he had already covered). Pure logic, no DB.

Run:  python test_book_done.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.report import classify_done, format_done   # noqa: E402

D1, D2 = dt.date(2026, 9, 25), dt.date(2026, 9, 28)
IND = "X96383748"
TRADES = [
    (D2, IND, "CAVA", "YOU BOUGHT SHORT COVER CAVA GROUP INC COM (CAVA) (Short)", -207.96),
    (D1, IND, "CAVA", "YOU BOUGHT SHORT COVER CAVA GROUP INC COM (CAVA) (Short)", -105.40),
    (D1, IND, "CRI", "YOU BOUGHT SHORT COVER CARTERS INC COM (CRI) (Short)", -61.17),
    (D2, IND, "WMT", "YOU SOLD SHORT SALE WALMART INC COM (WMT) (Short)", 108.80),
    (D2, IND, "NVDA", "YOU SOLD NVIDIA CORPORATION COM (NVDA) (Margin)", 74.93),
    (D2, IND, "PI", "YOU SOLD IMPINJ INC COM (PI) (Margin)", 353.66),
    (D2, IND, "MRVL", "YOU BOUGHT MARVELL TECHNOLOGY INC COM (MRVL) (Margin)", -149.78),
]
HELD = {("CRI", IND): -93.0, ("WMT", IND): -108.73, ("NVDA", IND): 282.87,
        ("MRVL", IND): 486.41}      # CAVA and PI no longer held


def test_cover_all_vs_some():
    d = classify_done(TRADES, HELD)
    assert "CAVA" in d["cover_all"] and round(d["cover_all"]["CAVA"]["usd"], 2) == 313.36
    assert d["cover_all"]["CAVA"]["last"] == D2
    assert "CRI" in d["cover_some"] and "CRI" not in d["cover_all"]


def test_short_buy_sell():
    d = classify_done(TRADES, HELD)
    assert "WMT" in d["short"]
    assert "MRVL" in d["buy"]
    assert "NVDA" in d["sell_some"] and "PI" in d["sell_all"]


def test_format_and_notes():
    lines, notes = format_done(classify_done(TRADES, HELD), [D1, D2])
    txt = "\n".join(lines)
    assert "COVERED ALL" in txt and "CAVA $313" in txt and "09/25–09/28" in lines[0]
    assert notes["CRI"].startswith("covered some $61")


def test_empty_window():
    lines, notes = format_done(classify_done([], {}), [])
    assert "no trades" in lines[-1] and notes == {}


if __name__ == "__main__":
    import inspect
    fails = 0
    for name, fn in sorted(inspect.getmembers(sys.modules["__main__"], inspect.isfunction)):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
