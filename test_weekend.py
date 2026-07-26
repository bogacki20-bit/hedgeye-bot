"""Fixture tests for weekend_report pure logic (no DB). Run: python test_weekend.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools.weekend_report import (lower_high_streak, aggregate_rotation,
                                   select_rolling_over, _flow_verdict,
                                   format_weekend_report, trailing_returns,
                                   vix_bucket, format_asset_table,
                                   detect_flip, dist_to_trend, format_changes,
                                   _coarse_sector, _is_hard_flip, _ret_dir,
                                   is_adverse_momentum, select_stop_adding,
                                   format_stop_adding, select_short_gate,
                                   select_puck, format_screens, _wow, _volmark)

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'OK' if ok else 'XX'} {label}: got {got!r}" + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


print("lower_high_streak — consecutive lower daily highs (newest last):")
check("3 lower highs off the top", lower_high_streak([10, 11, 10.5, 10.2, 9.8]), 3)
check("rising tape -> 0", lower_high_streak([1, 2, 3, 4, 5]), 0)
check("flat high stops the streak (equal is NOT lower)",
      lower_high_streak([5, 4, 4, 3]), 1)          # only 3<4; the 4==4 stops it
check("all lower -> full length-1", lower_high_streak([9, 8, 7, 6]), 3)
check("single bar -> 0", lower_high_streak([7]), 0)
check("empty -> 0", lower_high_streak([]), 0)
check("None gaps ignored", lower_high_streak([10, None, 9, 8]), 2)
check("newest ticked up -> 0 even after a slide",
      lower_high_streak([10, 9, 8, 8.5]), 0)

print("aggregate_rotation — per-sector flow, money-in ranked first:")
rows = [
    {"sector": "Energy", "trend": "bullish", "range_pos": 0.42, "1m": 0.05},
    {"sector": "Energy", "trend": "bullish", "range_pos": 0.44, "1m": 0.04},
    {"sector": "Energy", "trend": "neutral", "range_pos": None, "1m": None},
    {"sector": "Tech",   "trend": "bearish", "range_pos": 0.28, "1m": -0.05},
    {"sector": "Tech",   "trend": "bearish", "range_pos": 0.22, "1m": -0.04},
    {"sector": "Tech",   "trend": "bullish", "range_pos": 0.30, "1m": -0.02},
]
agg = aggregate_rotation(rows)
check("Energy ranks first (money in)", agg[0]["sector"], "Energy")
check("Energy net_trend +2 (neutral doesn't move it)", agg[0]["net_trend"], 2)
check("Energy n counts the neutral name too (=3)", agg[0]["n"], 3)
check("Energy flow ACCUM (breadth+ & momentum up)", agg[0]["flow"], "ACCUM")
check("Tech flow DISTRIB (breadth- & momentum down)", agg[-1]["flow"], "DISTRIB")
check("Energy avg_rp (neutral has no rp, unchanged)", agg[0]["avg_rp"], 0.43)
check("Tech last (money out)", agg[-1]["sector"], "Tech")
check("Tech net_trend -1 (2 bear, 1 bull)", agg[-1]["net_trend"], -1)
check("Tech flow DISTRIB", agg[-1]["flow"], "DISTRIB")

print("_flow_verdict — accum/distrib/hold gates:")
check("bull breadth + RS up -> ACCUM", _flow_verdict(3, "↑"), "ACCUM")
check("bear breadth + RS down -> DISTRIB", _flow_verdict(-2, "↓"), "DISTRIB")
check("bull breadth but RS flat -> hold", _flow_verdict(3, "→"), "hold")
check("mixed -> hold", _flow_verdict(-1, "↑"), "hold")

print("select_rolling_over — distribution watch (>= min_streak lower highs):")
rr = [
    {"ticker": "META",  "high_series": [10, 9.5, 9.2, 9.0, 8.7], "range_pos": 0.11,
     "rs_slope": -0.05, "held": True},
    {"ticker": "XLE",   "high_series": [5, 5.2, 5.4, 5.6],        "range_pos": 0.42,
     "rs_slope": 0.04,  "held": False},
    {"ticker": "GOOGL", "high_series": [20, 19.5, 19.2],          "range_pos": 0.23,
     "rs_slope": -0.02, "held": True},
]
ro = select_rolling_over(rr, min_streak=2)
check("only rolling-over names kept (META, GOOGL)", [d["ticker"] for d in ro],
      ["META", "GOOGL"])
check("META worst first (4d)", ro[0]["lh_streak"], 4)
check("held flag carried", ro[0]["held"], True)
check("rising XLE excluded", all(d["ticker"] != "XLE" for d in ro), True)

print("trailing_returns — 1w/1m/3m close-to-close:")
# 70 sessions rising 1%/session; base for 1w is 6 bars back, 1m 22, 3m 64
_cl = [100 * (1.01 ** i) for i in range(70)]
tr = trailing_returns(_cl)
check("1w ~ +5.1% (5 sessions @1%)", round(tr["1w"], 3), round(1.01**5 - 1, 3))
check("1m ~ +23% (21 sessions)", round(tr["1m"], 3), round(1.01**21 - 1, 3))
check("3m ~ +88% (63 sessions)", round(tr["3m"], 3), round(1.01**63 - 1, 3))
check("too-short series -> None for 3m", trailing_returns([1, 2, 3])["3m"], None)

print("vix_bucket — sizing regime:")
check("15.6 -> Investable", vix_bucket(15.6).startswith("Investable"), True)
check("22 -> Volatile", vix_bucket(22).startswith("Volatile"), True)
check("None -> ?", vix_bucket(None), "?")

print("aggregate_rotation carries sector-average returns:")
_rr = aggregate_rotation([
    {"sector": "Energy", "trend": "bullish", "range_pos": 0.42, "rs_slope": 0.05,
     "1w": 0.02, "1m": 0.05, "3m": 0.15},
    {"sector": "Energy", "trend": "bullish", "range_pos": 0.40, "rs_slope": 0.04,
     "1w": 0.04, "1m": 0.07, "3m": 0.17},
])
check("Energy avg 1w = 3%", round(_rr[0]["1w"], 3), 0.03)
check("Energy avg 3m = 16%", round(_rr[0]["3m"], 3), 0.16)

print("format_asset_table — per-asset returns, sorted by 1m desc:")
_at = format_asset_table([
    {"ticker": "XLE", "sector": "Energy", "trend": "bullish", "range_pos": 0.42,
     "1w": 0.02, "1m": 0.05, "3m": 0.15, "lh_streak": 0, "held": False},
    {"ticker": "META", "sector": "Comm Svcs", "trend": "bearish", "range_pos": 0.11,
     "1w": -0.01, "1m": -0.03, "3m": -0.08, "lh_streak": 5, "held": True},
], sort_key="1m")
check("XLE (best 1m) sorts above META", _at.index("XLE") < _at.index("META"), True)
check("held book flag renders", "📗" in _at, True)
check("percent formatting present", "+5.0%" in _at, True)

print("detect_flip — trend/momentum sign changes:")
check("bull -> bear flips", detect_flip("trendBearish", "trendBullish"), "bull→bear")
check("bear -> bull flips", detect_flip("momentumBullish", "momentumBearish"), "bear→bull")
check("bull -> bull no flip", detect_flip("trendBullish", "trendBullish"), None)
check("bull -> neutral flips", detect_flip("trendNeutral", "trendBullish"), "bull→neut")

print("dist_to_trend — % to nearer long-term boundary:")
check("near support -> support edge", dist_to_trend(102, 100, 130), (2.0, "support"))
check("near resistance -> resistance edge", dist_to_trend(128, 100, 130)[1], "resistance")
check("missing data -> (None, None)", dist_to_trend(None, 1, 2), (None, None))

print("format_changes — what-changed section:")
_fc = format_changes([{"ticker": "GOOGL", "flip": "bull→bear"}],
                     [{"ticker": "META", "flip": "bull→bear"}])
check("counts both flip types", "1 trend · 1 momentum" in _fc, True)
check("names a trend flip", "GOOGL bull→bear" in _fc, True)

print("_coarse_sector — GICS for equities, coarse class for the rest:")
check("gics wins", _coarse_sector("XOM", "Energy", "whatever", None), "Energy")
check("precious metals -> Commodities", _coarse_sector("GLD", None, "Precious Metals", None), "Commodities")
check("bond fund -> Fixed Income", _coarse_sector("SHY", None, "Ultrashort Bond", None), "Fixed Income")
check("single currency -> Currency", _coarse_sector("FXE", None, "Single Currency", None), "Currency")
check("digital assets -> Crypto", _coarse_sector("ETHA", None, "Digital Assets", None), "Crypto")
check("unknown non-gics -> ETF/Thematic", _coarse_sector("MTUM", None, "Mid-Cap Value", None), "ETF/Thematic")
check("CL_F future -> Commodities (no subsector tag)", _coarse_sector("CL_F", None, None, None), "Commodities")
check("ZN_F future -> Fixed Income", _coarse_sector("ZN_F", None, None, None), "Fixed Income")
check("6E_F future -> Currency", _coarse_sector("6E_F", None, None, None), "Currency")
check("ES_F future -> Equity-Index", _coarse_sector("ES_F", None, None, None), "Equity-Index")

print("_is_hard_flip / _ret_dir:")
check("bull->bear is hard", _is_hard_flip("bull→bear"), True)
check("neut->bull is soft", _is_hard_flip("neut→bull"), False)
check("ret up -> ↑", _ret_dir(0.02), "↑")
check("ret down -> ↓", _ret_dir(-0.02), "↓")
check("tiny ret -> flat", _ret_dir(0.001), "→")

print("format_changes — hard listed, soft summarized, capped:")
_fc2 = format_changes([{"ticker": "AAA", "flip": "bull→bear"}], [], trend_soft=40)
check("soft neutrals summarized as a count", "[+40 to/from neutral]" in _fc2, True)
_many = [{"ticker": f"T{i}", "flip": "bull→bear"} for i in range(40)]
check("caps the list with +N more", "(+10 more)" in format_changes(_many, [], cap=30), True)

print("is_adverse_momentum — the stop-adding trigger:")
check("long, momentum bull->neut = adverse", is_adverse_momentum("long", "momentumBullish", "momentumNeutral"), True)
check("long, momentum bull->bear = adverse", is_adverse_momentum("long", "momentumBullish", "momentumBearish"), True)
check("long, momentum bear->bull = NOT adverse (helps a long)", is_adverse_momentum("long", "momentumBearish", "momentumBullish"), False)
check("short, momentum bear->bull = adverse", is_adverse_momentum("short", "momentumBearish", "momentumBullish"), True)
check("short, momentum bull->bear = NOT adverse (helps a short)", is_adverse_momentum("short", "momentumBullish", "momentumBearish"), False)
check("no change = not adverse", is_adverse_momentum("long", "momentumBullish", "momentumBullish"), False)

print("select_stop_adding — held positions with momentum turning against them:")
_sa = select_stop_adding([
    {"ticker": "MSFT", "side": "long", "momo_prev": "momentumBullish",
     "momo_now": "momentumBearish", "range_pos": 0.22},
    {"ticker": "XLE", "side": "long", "momo_prev": "momentumBullish",
     "momo_now": "momentumBullish", "range_pos": 0.42},   # unchanged -> excluded
    {"ticker": "TLT", "side": "short", "momo_prev": "momentumBearish",
     "momo_now": "momentumNeutral", "range_pos": 0.30},   # short weakening -> flagged
])
check("MSFT flagged (long, momo bull->bear)", "MSFT" in [d["ticker"] for d in _sa], True)
check("XLE not flagged (momentum unchanged)", "XLE" not in [d["ticker"] for d in _sa], True)
check("TLT short flagged (bear->neut)", "TLT" in [d["ticker"] for d in _sa], True)
check("flip label rendered", _sa[0]["flip"], "bull→bear")
_saf = format_stop_adding(_sa)
check("section says stop adding", "STOP ADDING" in _saf and "don't add" in _saf, True)

print("select_short_gate / select_puck — pre-computed screens:")
_sg_assets = [
    {"ticker": "NFLX", "trend": "bearish", "range_pos": 0.85, "sector": "Comm", "lh_streak": 4},
    {"ticker": "XYZ", "trend": "bearish", "range_pos": 0.50, "sector": "Fin"},          # rp too low
    {"ticker": "AAA", "trend": "bullish", "range_pos": 0.90, "sector": "Tech"},          # not bear
]
_sg = select_short_gate(_sg_assets)
check("SHORT GATE keeps bear+rp>=0.80 only", [a["ticker"] for a in _sg], ["NFLX"])
_puck_assets = [
    {"ticker": "XLE", "trend": "bullish", "range_pos": 0.30, "sector": "Energy", "held": True},
    {"ticker": "CVX", "trend": "bullish", "range_pos": 0.30, "sector": "Energy", "held": False},  # not held
    {"ticker": "MSFT", "trend": "bullish", "range_pos": 0.30, "sector": "Tech", "held": True},     # sector not top-half
]
_pk = select_puck(_puck_assets, top_half_sectors={"Energy"})
check("PUCK keeps own+bull+rp<0.5+top-half sector", [a["ticker"] for a in _pk], ["XLE"])
_scr = format_screens(_sg, _pk)
check("screens: SHORT GATE qualifier count", "1 qualifier" in _scr, True)
check("empty screen prints 0 qualifiers", "0 qualifiers" in format_screens([], []), True)

print("_volmark — volume overlay (real_dip, price_down_3d, decel, streak):")
check("real_dip -> decel dip tag", _volmark((True, True, True, 3)), "↓3d")
check("down on heavy (non-decel) vol -> distribution ↑", _volmark((False, True, False, 0)), "↑")
check("no signal -> blank", _volmark((False, False, False, 0)), "")
check("None -> blank", _volmark(None), "")

print("_wow — sector week-over-week rank tag:")
check("moved up 5->1", _wow({"rank": 1, "prev_rank": 5}), "5→1")
check("unchanged =", _wow({"rank": 3, "prev_rank": 3}), "=")
check("no prior -> ·new", _wow({"rank": 2, "prev_rank": None}), "·new")

print("format_weekend_report — full-universe render:")
_regime = {"date": "Sun Jul 26", "monthly_quad": "Quad 4", "quarterly_quad": "Quad 4",
           "vix": "15.6", "vix_bucket": "Investable→full size", "n_names": 613,
           "n_sectors": 11, "pct_bull": 41, "pct_bear": 38, "pct_neut": 21}
_uni = [
    {"sector": "Energy", "trend": "bullish", "range_pos": 0.44, "rs_slope": 0.05,
     "1w": 0.021, "1m": 0.047, "3m": 0.155},
    {"sector": "Energy", "trend": "bullish", "range_pos": 0.40, "rs_slope": 0.04,
     "1w": 0.018, "1m": 0.041, "3m": 0.148},
    {"sector": "Utilities", "trend": "bullish", "range_pos": 0.55, "rs_slope": 0.03,
     "1w": 0.010, "1m": 0.028, "3m": 0.062},
    {"sector": "REITs", "trend": "bullish", "range_pos": 0.48, "rs_slope": 0.00,
     "1w": 0.005, "1m": 0.028, "3m": 0.041},
    {"sector": "Tech", "trend": "bearish", "range_pos": 0.28, "rs_slope": -0.06,
     "1w": -0.020, "1m": -0.055, "3m": -0.090},
    {"sector": "Tech", "trend": "bearish", "range_pos": 0.22, "rs_slope": -0.05,
     "1w": -0.030, "1m": -0.048, "3m": -0.110},
    {"sector": "Tech", "trend": "bullish", "range_pos": 0.30, "rs_slope": -0.02,
     "1w": -0.008, "1m": -0.020, "3m": -0.030},
]
_rot = aggregate_rotation(_uni)
for _i, _s in enumerate(_rot):
    _s["rank"] = _i + 1
    _s["prev_rank"] = {"Energy": 5}.get(_s["sector"])   # Energy 5th -> 1st
_assets = [
    {"ticker": "XLE", "sector": "Energy", "trend": "bullish", "range_pos": 0.42,
     "rs_rank": 2, "1w": 0.021, "1m": 0.047, "3m": 0.155, "lh_streak": 0, "held": False,
     "vs_trend": 3.2, "trend_edge": "support", "iv": 0.28, "rv": 0.21, "ivpd": 0.31,
     "vol": (False, True, False, 0)},
    {"ticker": "XLU", "sector": "Utilities", "trend": "bullish", "range_pos": 0.55,
     "rs_rank": 6, "1w": 0.010, "1m": 0.028, "3m": 0.062, "lh_streak": 0, "held": False,
     "vs_trend": 5.0, "trend_edge": "support"},
    {"ticker": "META", "sector": "Comm Svcs", "trend": "bearish", "range_pos": 0.11,
     "rs_rank": 51, "1w": -0.030, "1m": -0.055, "3m": -0.110, "lh_streak": 4, "held": True,
     "vs_trend": 1.4, "trend_edge": "resistance"},
    {"ticker": "TSLA", "sector": "Consumer Disc", "trend": "bearish", "range_pos": 0.13,
     "rs_rank": 47, "1w": -0.020, "1m": -0.048, "3m": -0.090, "lh_streak": 3, "held": False,
     "vs_trend": 2.1, "trend_edge": "resistance"},
]
_tflips = [{"ticker": "GOOGL", "flip": "bull→bear"}, {"ticker": "XLE", "flip": "neut→bull"}]
_mflips = [{"ticker": "META", "flip": "bull→bear"}]
_roll = select_rolling_over([
    {"ticker": "META", "high_series": [10, 9.6, 9.3, 9.0, 8.7], "range_pos": 0.11,
     "rs_slope": -0.05, "held": True},
    {"ticker": "TSLA", "high_series": [7, 6.8, 6.6, 6.5], "range_pos": 0.13,
     "rs_slope": -0.04, "held": False},
], min_streak=3)
_lead = [{"ticker": "XLE", "rs_rank": 2, "hurst": 0.68, "range_pos": 0.42}]
_book = {"held_count": 31, "with_flow": ["WMT-short", "XLE"],
         "against": ["MSFT 📗 (RS↓, 2d ↓highs)"]}
_stopadd = [{"ticker": "MSFT", "side": "long", "flip": "bull→bear", "range_pos": 0.22}]
_footer = {"book_full": "AAA  IND  1.0%  1.5% set  67%  equity  +2.1%",
           "cash": "  Individual: $5,000", "book_risk": "  BOOK RISK (60d): 55 positions ≈ 30 bets",
           "roro": "  HYG/TLT +1.2% risk-on↑", "diversification": "  60d avg pairwise sector corr: 0.42 (loose)"}
_txt = format_weekend_report({
    "regime": _regime, "stop_adding": _stopadd,
    "book_reconciled": "2026-07-25", "dark_held": ["2513.HK", "2408.TW"],
    "trend_flips": _tflips, "momo_flips": _mflips,
    "rotation": _rot, "assets": _assets, "rolling": _roll,
    "short_gate": [{"ticker": "NFLX", "range_pos": 0.85, "sector": "Comm", "lh_streak": 4}],
    "puck": [{"ticker": "XLE", "range_pos": 0.30, "sector": "Energy"}],
    "leaders": _lead, "book": _book, "footer": _footer,
})
check("header names the universe size", "613 names" in _txt, True)
check("quad header monthly+quarterly", "QUAD: monthly=Quad 4 quarterly=Quad 4" in _txt, True)
check("legend defines vsTR/flow", "LEGEND" in _txt and "vsTR" in _txt and "ACCUM when" in _txt, True)
check("book source stamp present", "book source: bot · last reconciled: 2026-07-25" in _txt, True)
check("dark held footnote present", "dark (held, no live range" in _txt and "2513.HK" in _txt, True)
check("iv/rv/ivpd/vol columns in asset table", all(c in _txt for c in ("iv", "rv", "ivpd", "vol")), True)
check("WoW rank delta renders (Energy 5->1)", "5→1" in _txt, True)
check("pre-computed screens render", "SHORT GATE" in _txt and "PUCK" in _txt, True)
check("BOOK STATE footer renders", "BOOK STATE" in _txt and "BOOK RISK" in _txt and "RORO" in _txt, True)
check("STOP ADDING section renders with held name", "STOP ADDING" in _txt and "MSFT" in _txt, True)
check("what-changed section renders", "WHAT CHANGED" in _txt and "GOOGL bull→bear" in _txt, True)
check("a sector row renders", "Energy" in _txt and "ACCUM" in _txt, True)
check("asset table has vsTREND column", "vsTR" in _txt, True)
check("rolling-over section counts names", "ROLLING OVER — 2 names" in _txt, True)
check("book overlay renders", "31 held" in _txt, True)
print("\n----- RENDERED SAMPLE -----")
print(_txt)
print("----- END SAMPLE -----")

print(f"\n{str(FAIL) + ' FAILURES' if FAIL else 'all passed'}")
sys.exit(1 if FAIL else 0)
