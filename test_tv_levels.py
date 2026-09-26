"""test_tv_levels.py — TradingView TRADE/TREND blend + Yahoo quote mapping.

Run:  python test_tv_levels.py      (pure logic, no DB, no network)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.quotes import yahoo_symbol   # noqa: E402
from tools.tt_levels import tt_tag      # noqa: E402
from tools.tv_levels import derive      # noqa: E402
from tools.tv_symbols import tv_symbol  # noqa: E402


def test_spx_9_17_reproduces_keiths_alpha_code():
    """The case that set the rule: Keith 9/17 TREND 7501 / TRADE 7651.
    MFR Trend daily 7609.21/7501.67, Vola2vola daily 7571.60/7650.61."""
    d = derive(7609.21, 7501.67, 7571.60, 7650.61, "BULLISH")
    assert abs(d["trend"] - 7501) < 1 and abs(d["trade"] - 7651) < 1, d


def test_bearish_is_the_mirror():
    d = derive(60.20, 58.59, 61.67, 63.21, "BEARISH")
    assert d["trend"] == 60.20 and d["trade"] == 61.67 and d["basis"] == "bear-mirror"


def test_missing_vola_plot_uses_the_one_present():
    d = derive(69.63, 64.78, None, 53.07, "BULLISH")        # CAVA 9/25: plot 1 empty
    assert d["trade"] == 53.07 and d["trend"] == 64.78


def test_tag_format():
    assert tt_tag(63.21, 58.59) == " TRD 63.21 TND 58.59"
    assert tt_tag(7650.61, 7501.67) == " TRD 7,651 TND 7,502"
    assert tt_tag(None, None) == ""
    assert tt_tag(61.67, 60.20, "bear-mirror").endswith("·mir")


def test_tv_symbol_map():
    assert tv_symbol("XLE") == "BATS:XLE"
    assert tv_symbol("CORN") == "BATS:CORN"        # not the WHSELFINVEST corn CFD
    assert tv_symbol("GTBIF") == "OTC:GTBIF"
    assert tv_symbol("CL_F") == "NYMEX:CL1!"
    assert tv_symbol("FDAX_F") == "EUREX:FDAX1!"
    assert tv_symbol("MC.PA") == "EURONEXT:MC"
    assert tv_symbol("0700.HK") == "HKEX:700"
    assert tv_symbol("VOLV-B.ST") == "OMXSTO:VOLV_B"
    assert tv_symbol("BRENT") == "ICEEUR:BRN1!"
    assert tv_symbol("^VIX") == "CBOE:VIX"


def test_yahoo_symbol_map():
    assert yahoo_symbol("CL_F") == "CL=F"
    assert yahoo_symbol("FDAX_F") is None                   # Eurex: no Yahoo quote
    assert yahoo_symbol("EUR/USD") == "EURUSD=X"
    assert yahoo_symbol("ETHUSD") == "ETH-USD"
    assert yahoo_symbol("XLE") == "XLE"


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
            except Exception as e:
                fails += 1
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
