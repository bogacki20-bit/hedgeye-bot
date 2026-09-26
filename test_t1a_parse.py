"""test_t1a_parse.py — T1A validator + SG Founder's Note level parser.

Run:  python test_t1a_parse.py      (pure logic, no DB, no network)
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser_t1a import validate          # noqa: E402
from tools.sg_index_levels import parse_note  # noqa: E402

GOOD = {"report_date": "2026-09-25", "data_date": "2026-09-24",
        "gamma_exposure": "Positive", "systematic_flow_risk": "Neutral",
        "pv_band_rr": "Neutral", "strategic_allocation": "Risk On",
        "spx_last": 7704.13, "upper_pv_band": 7819.0, "lower_pv_band": 7613.0,
        "gex_throttle": 2.67, "gex_flip": 7671.26, "resistance_strike": 7800,
        "focal_strike": 7700, "support_strike": 7650, "sys_positioning_z": 0.2,
        "vc_flow_by_move": {str(i): 0.0 for i in range(-5, 6)}}


def test_real_9_25_values_pass():
    hard, warn = validate(copy.deepcopy(GOOD))
    assert not hard and not warn, (hard, warn)


def test_dropped_digit_on_the_flip_is_blocked():
    d = copy.deepcopy(GOOD)
    d["gex_flip"] = 767.26                    # 7671.26 read without a digit
    hard, _ = validate(d)
    assert any("gex_flip" in h for h in hard), hard


def test_unknown_status_word_is_blocked():
    d = copy.deepcopy(GOOD)
    d["gamma_exposure"] = "Positve"
    assert validate(d)[0]


def test_inverted_pv_band_is_blocked():
    d = copy.deepcopy(GOOD)
    d["upper_pv_band"], d["lower_pv_band"] = 7613.0, 7819.0
    assert any("inverted" in h for h in validate(d)[0])


def test_wild_z_score_is_dropped_not_stored():
    d = copy.deepcopy(GOOD)
    d["sys_positioning_z"] = 20
    hard, warn = validate(d)
    assert not hard and d["sys_positioning_z"] is None and warn


NOTE = """
\t
/ESZ26
\t
SPX
\t
SPY
\t
NDX
\t
QQQ
\t
RUT
\t
IWM
Reference Price:
\t
$7767.2
\t
$7704
\t
$767
\t
$30478
\t
$741
\t
$2835
\t
$281
Zero Gamma Level:
\t
$7722.2
\t
$7659
\t
$765
\t
$29947
\t
$734
\t
$2937
\t
$290
Gamma Tilt:
\t
1.165
\t
0.854
\t
1.62
\t
1.073
\t
0.309
\t
0.392
Gamma Notional (MM):
\t
$493.101M
\t
‑$442.262M
\t
$13.77M
\t
$274.193M
\t
‑$155.712M
\t
‑$1.504B
Pivot: 7,690 (bearish <, bullish >)
"""


def test_sg_note_columns_map_to_the_right_symbols():
    r = parse_note(NOTE)
    assert r["SPX"]["zero_gamma"] == 7659 and r["ES"]["zero_gamma"] == 7722.2
    assert r["SPX"]["gamma_tilt"] == 1.165 and r["IWM"]["gamma_tilt"] == 0.392
    assert "gamma_tilt" not in r["ES"]              # 6-value rows skip /ES


def test_sg_notional_units_and_sign():
    r = parse_note(NOTE)
    assert r["SPY"]["gamma_notional_mm"] == -442.262
    assert r["IWM"]["gamma_notional_mm"] == -1504.0  # $1.504B -> $MM
    assert r["SPX"]["risk_pivot"] == 7690


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
