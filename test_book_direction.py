"""
test_book_direction.py — fixture tests for the position-side derivation
(tools/book_direction.compute_sides). Pure Python, no DB. Run directly:
    python test_book_direction.py
Cases mirror the live book: SHY/TUA/HEFT/AGGH longs, EIS/PPLT outright shorts,
bear put spreads (AMZN-class), SBIT inverse wrapper, covered call, flat spread,
unjudgeable legs, multi-account aggregation, missing market_value fallback.
"""
from tools.book_direction import compute_sides


def eq(underlying, qty, mv, acct="X1"):
    return {"underlying": underlying, "asset_class": "equity", "is_option": False,
            "opt_type": None, "quantity": qty, "market_value": mv,
            "account_number": acct}


def opt(underlying, qty, mv, cp):
    return {"underlying": underlying, "asset_class": "option", "is_option": True,
            "opt_type": cp, "quantity": qty, "market_value": mv}


def test_long_shares_are_long():
    for t in ("SHY", "TUA", "HEFT", "AGGH"):
        s = compute_sides([eq(t, 100, 5000.0)])
        assert s[t]["side"] == "long", (t, s[t])
        assert s[t]["raw_side"] == "long"


def test_short_shares_are_short():
    for t in ("EIS", "PPLT"):
        s = compute_sides([eq(t, -50, -2500.0)])
        assert s[t]["side"] == "short", (t, s[t])


def test_bear_put_spread_is_short():
    # long put (bigger premium) + short lower-strike put, equal quantity
    s = compute_sides([opt("AMZN", 1, 500.0, "P"), opt("AMZN", -1, -300.0, "P")])
    assert s["AMZN"]["side"] == "short", s["AMZN"]
    assert s["AMZN"]["legs"] == 2


def test_bull_put_credit_spread_is_long():
    s = compute_sides([opt("XLE", -1, -400.0, "P"), opt("XLE", 1, 150.0, "P")])
    assert s["XLE"]["side"] == "long", s["XLE"]


def test_long_call_is_long_long_put_is_short():
    assert compute_sides([opt("NVDA", 2, 800.0, "C")])["NVDA"]["side"] == "long"
    assert compute_sides([opt("EWZ", 3, 900.0, "P")])["EWZ"]["side"] == "short"


def test_covered_call_stays_long():
    s = compute_sides([eq("XLV", 100, 10000.0), opt("XLV", -1, -200.0, "C")])
    assert s["XLV"]["side"] == "long", s["XLV"]


def test_inverse_wrapper_flips_exposure_side():
    links = {"SBIT": {"underlying": "BTCUSD", "inverse": True}}
    s = compute_sides([eq("SBIT", 200, 4000.0)], links)
    assert s["SBIT"]["raw_side"] == "long"
    assert s["SBIT"]["side"] == "short"          # short-BTC expression
    assert s["SBIT"]["via_linkage"] is True
    # non-inverse wrapper does NOT flip
    links2 = {"GLDX": {"underlying": "GLD", "inverse": False}}
    s2 = compute_sides([eq("GLDX", 10, 1000.0)], links2)
    assert s2["GLDX"]["side"] == "long"


def test_flat_spread_reports_flat_never_guesses():
    s = compute_sides([opt("SLV", 1, 250.0, "P"), opt("SLV", -1, -250.0, "P")])
    assert s["SLV"]["side"] == "flat", s["SLV"]


def test_unjudgeable_legs_are_counted_loudly():
    rows = [opt("MYST", 1, 300.0, None),     # option with no C/P
            eq("MYST", None, 100.0)]         # no quantity
    s = compute_sides(rows)
    assert s["MYST"]["raw_side"] is None     # every leg unjudgeable -> no verdict
    assert s["MYST"]["unknown_legs"] == 2


def test_multi_account_aggregation():
    s = compute_sides([eq("SHY", 100, 8000.0, "X1"), eq("SHY", 50, 4000.0, "R2")])
    assert s["SHY"]["side"] == "long"
    assert s["SHY"]["legs"] == 2


def test_missing_market_value_falls_back_to_qty():
    s = compute_sides([eq("ZROZ", 40, None)])
    assert s["ZROZ"]["side"] == "long"


def test_shares_dominate_hedge_leg_weighting():
    # long shares with a protective put: still net long
    s = compute_sides([eq("VCLT", 200, 15000.0), opt("VCLT", 2, 600.0, "P")])
    assert s["VCLT"]["side"] == "long", s["VCLT"]


def test_side_stamp_variants():
    import time as _t
    import tools.book_direction as bd
    sides = {
        "XLV":  {"side": "long", "raw_side": "long", "net": 1.0, "legs": 1,
                 "unknown_legs": 0, "via_linkage": False},
        "SBIT": {"side": "short", "raw_side": "long", "net": 1.0, "legs": 1,
                 "unknown_legs": 0, "via_linkage": True},
    }
    links = {"SBIT": {"underlying": "BTCUSD", "inverse": True},
             "METD": {"underlying": "META", "inverse": True}}
    bd._stamp_cache.update({"exp": _t.time() + 60, "sides": sides,
                            "links": links, "failed": False})
    assert bd.side_stamp("XLV") == "📗 YOU HOLD XLV: LONG"
    assert "SHORT exposure" in bd.side_stamp("SBIT")
    assert "SBIT" in bd.side_stamp("BTCUSD")        # reverse: held wrapper on it
    assert bd.side_stamp("META") == ""              # METD not held here
    assert bd.side_stamp("NVDA") == ""
    bd._stamp_cache.update({"failed": True})
    assert "FAILED" in bd.side_stamp("XLV")         # loud, never silent
    bd._stamp_cache.update({"exp": 0.0, "failed": False})


if __name__ == "__main__":
    import sys, inspect
    fails = 0
    mod = sys.modules["__main__"]
    for name, fn in sorted(inspect.getmembers(mod, inspect.isfunction)):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
    print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURE(S)'}")
    sys.exit(1 if fails else 0)
