"""Fixture tests for tools/rta_cross_signal.py pure parts (no DB).
    python test_rta_cross_signal.py
"""
from tools.rta_cross_signal import CLOSE_TYPES, build_matches, format_alert


def _sides():
    mk = lambda side, raw=None, link=False: {
        "side": side, "raw_side": raw or side, "net": 1.0, "legs": 1,
        "unknown_legs": 0, "via_linkage": link}
    return {"XLF": mk("long"), "IAK": mk("long"), "EIS": mk("short"),
            "METD": mk("short", raw="long", link=True)}


LINKS = {"METD": {"underlying": "META", "inverse": True},
         "SBIT": {"underlying": "BTCUSD", "inverse": True}}


def test_close_types_exclude_trims():
    assert "sell" in CLOSE_TYPES and "cover" in CLOSE_TYPES
    assert "sell-some" not in CLOSE_TYPES and "cover-some" not in CLOSE_TYPES


def test_direct_and_sector_match():
    rec = {"ticker": "XLF", "signal_type": "sell", "side": "short"}
    m = build_matches(rec, _sides(), LINKS, {"XLF"},
                      sector_pair=("Financials", None),
                      sector_book={"XLF": None, "IAK": "insurance"},
                      rps={"XLF": 0.81, "IAK": 0.71})
    assert m["direct"]["ticker"] == "XLF" and m["direct"]["side"] == "long"
    assert m["on_ss"] is True
    assert [x["ticker"] for x in m["sector"]] == ["IAK"]
    title, body = format_alert(rec, m, closed=True)
    assert "SELL XLF" in title
    assert "YOU HOLD XLF: LONG" in body and "take-profit check" in body
    assert "IAK(long" in body and "rp=0.71" in body
    assert "removed from today's alert universe" in body


def test_wrapper_reverse_match_buy_is_scale_in():
    rec = {"ticker": "META", "signal_type": "buy", "side": "long",
           "analyst": "Tobin", "note": "Long Comms", "price": 402.5}
    m = build_matches(rec, _sides(), LINKS, set(),
                      sector_pair=("Communication Services", "internet"),
                      sector_book={}, rps={"METD": 0.06})
    assert m["direct"] is None
    assert len(m["wrappers"]) == 1 and m["wrappers"][0]["ticker"] == "METD"
    assert m["wrappers"][0]["side"] == "short" and m["wrappers"][0]["inverse"]
    title, body = format_alert(rec, m, closed=False)
    assert "BUY META" in title
    assert "EXPOSURE via METD (short META ↯inv)" in body
    assert "scale-in check" in body and "↯inv" in body
    assert "removed" not in body
    assert "Tobin" in body and "402.5" in body


def test_no_match_no_noise():
    rec = {"ticker": "CAT", "signal_type": "buy", "side": "long"}
    m = build_matches(rec, _sides(), LINKS, set(),
                      sector_pair=(None, None), sector_book={}, rps={})
    assert m["direct"] is None and not m["wrappers"] and not m["sector"]
    assert m["on_ss"] is False


def test_flat_and_none_sides_never_match():
    sides = {"XLY": {"side": "flat", "raw_side": "flat", "net": 0, "legs": 2,
                     "unknown_legs": 0, "via_linkage": False}}
    rec = {"ticker": "XLY", "signal_type": "sell", "side": "short"}
    m = build_matches(rec, sides, {}, set(), sector_pair=(None, None),
                      sector_book={"XLY": None}, rps={})
    assert m["direct"] is None and not m["sector"]


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
