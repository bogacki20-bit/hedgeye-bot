"""End-to-end smoke for the side-aware trend+momentum gate (2026-05-28).

Twelve scenarios covering every interesting cell:
  LONG-side  × 5 zones (bottom_edge multi-gate, top_edge multi-gate, breakouts)
  SHORT-side × 5 zones (mirror cells)
  Plus 2 conflict scenarios where Haiku stubbed-returns the wrong verb
  so we verify Python's safety net.

Read-only diagnostic; delete after use.
"""
import decision_engine as de
import proactive_scanner


# scenarios: (label, ticker, side_flag_or_None, data, haiku_reply)
S = [
    # ============ LONG SIDE (unchanged behavior — sanity) ============
    ("L1 NVDA  long bottom_edge bullish+bullish → BUY", "NVDA",
        "etf_pro_long", {
        "price": 207.50, "rr_lo": 206.0, "rr_hi": 236.0,
        "mfr_lo": 205.63, "mfr_hi": 243.49,
        "trend": "trendBullish", "momentum": "momentumBullish",
        "cw": 245, "pw": 200,
     }, "[ETF Pro Long] BUY NVDA — 5% of RR — bottom-edge scale-in"),

    ("L2 BTC long bottom_edge neutral/bearish → WATCH", "BITCOIN",
        None, {
        "price": 72500.0, "rr_lo": None, "rr_hi": None,
        "mfr_lo": 72365.68, "mfr_hi": 76744.88,
        "trend": "trendNeutral", "momentum": "momentumBearish",
        "cw": None, "pw": None,
     }, "[Quad] BUY BITCOIN — 3% of MFR — bottom-edge scale-in"),

    ("L3 HYG long top_edge bullish+neutral → SELL", "HYG",
        "etf_pro_long", {
        "price": 80.42, "rr_lo": 79.33, "rr_hi": 80.49,
        "mfr_lo": 79.28, "mfr_hi": 80.49,
        "trend": "trendBullish", "momentum": "momentumNeutralDanger",
        "cw": 81, "pw": None,
     }, "[ETF Pro Long] SELL HYG — top-edge trim"),

    ("L4 TSN  long below_range → SELL (trend breakdown)", "TSN",
        None, {
        "price": 62.67, "rr_lo": 64.44, "rr_hi": 66.87,
        "mfr_lo": 64.44, "mfr_hi": 66.87,
        "trend": "trendBullish", "momentum": "momentumNeutral",
        "cw": None, "pw": None,
     }, "[Quad] BUY TSN — contrarian add"),    # Haiku is WRONG — Python overrides

    ("L5 CAD long above_range → BUY (trend continuation)", "CAD/USD",
        None, {
        "price": 0.7340, "rr_lo": 0.7250, "rr_hi": 0.7320,
        "mfr_lo": None, "mfr_hi": None,
        "trend": "trendBullish", "momentum": "momentumBullish",
        "cw": None, "pw": None,
     }, "[Risk Range] SELL CAD/USD — fade"),    # Haiku is WRONG — Python overrides

    # ============ SHORT SIDE (NEW BEHAVIOR — the operator's ask) ============
    ("S1 TLT  short top_edge bearish+bearish → SHORT", "TLT",
        "etf_pro_short", {
        "price": 95.10, "rr_lo": 90.0, "rr_hi": 95.80,
        "mfr_lo": 89.0, "mfr_hi": 96.0,
        "trend": "trendBearish", "momentum": "momentumBearish",
        "cw": None, "pw": 90,
     }, "[ETF Pro Short] SELL TLT — top-edge trim"),  # Haiku says SELL; should be SHORT

    ("S2 XLU  short top_edge bearish+neutral → WATCH", "XLU",
        "etf_pro_short", {
        "price": 45.50, "rr_lo": 42.0, "rr_hi": 46.0,
        "mfr_lo": 41.5, "mfr_hi": 46.5,
        "trend": "trendBearish", "momentum": "momentumNeutralDanger",
        "cw": 47, "pw": None,
     }, "[ETF Pro Short] SELL XLU — top-edge trim"),  # Should override to WATCH

    ("S3 INDA short top_edge bullish trend → AVOID", "INDA",
        "etf_pro_short", {
        "price": 52.60, "rr_lo": 48.0, "rr_hi": 53.0,    # 92% of RR — top_edge
        "mfr_lo": 47.8, "mfr_hi": 53.2,
        "trend": "trendBullish", "momentum": "momentumBullish",
        "cw": None, "pw": None,
     }, "[ETF Pro Short] SELL INDA — top-edge trim"),  # Should override to AVOID

    ("S4 EWZ  short bottom_edge bearish+bearish → HOLD", "EWZ",
        "etf_pro_short", {
        "price": 27.10, "rr_lo": 27.0, "rr_hi": 31.0,
        "mfr_lo": 26.8, "mfr_hi": 31.2,
        "trend": "trendBearish", "momentum": "momentumBearish",
        "cw": None, "pw": None,
     }, "[ETF Pro Short] BUY EWZ — bottom-edge scale-in"),  # Should override to HOLD

    ("S5 EWZ  short bottom_edge bearish+neutral → COVER", "EWZ",
        "etf_pro_short", {
        "price": 27.10, "rr_lo": 27.0, "rr_hi": 31.0,
        "mfr_lo": 26.8, "mfr_hi": 31.2,
        "trend": "trendBearish", "momentum": "momentumNeutral",
        "cw": None, "pw": None,
     }, "[ETF Pro Short] BUY EWZ — bottom-edge scale-in"),  # Should override to COVER

    ("S6 TLT  short above_range → COVER (thesis broken)", "TLT",
        "etf_pro_short", {
        "price": 96.50, "rr_lo": 90.0, "rr_hi": 95.80,
        "mfr_lo": 89.0, "mfr_hi": 95.0,
        "trend": "trendBullish", "momentum": "momentumBullish",
        "cw": None, "pw": None,
     }, "[ETF Pro Short] BUY TLT — trend continuation"),  # Should override to COVER

    ("S7 TLT  short below_range → SHORT (continuation add)", "TLT",
        "etf_pro_short", {
        "price": 89.20, "rr_lo": 90.0, "rr_hi": 95.80,
        "mfr_lo": 89.5, "mfr_hi": 95.0,
        "trend": "trendBearish", "momentum": "momentumBearish",
        "cw": None, "pw": 90,
     }, "[ETF Pro Short] SELL TLT — trend breakdown"),  # Should override to SHORT
]


class FakeClient:
    haiku_reply = ""
    def __init__(self, *a, **kw): pass
    class messages:
        @staticmethod
        def create(**kw):
            class R:
                content = [type("B", (), {"type": "text",
                                          "text": FakeClient.haiku_reply})()]
            return R()


import anthropic
anthropic.Anthropic = FakeClient

orig_rr  = de._get_risk_range
orig_mfr = de._get_mfr_latest
orig_yh  = de._get_yahoo_latest
orig_sf  = de.__dict__.get("source_flags_for", None)

# Stub source_flags_for so each scenario hits the side we want.
import tools.active_slice
orig_active_sf = tools.active_slice.source_flags_for


for label, ticker, side_flag, d, haiku_text in S:
    print("=" * 80)
    print(label)
    print("=" * 80)
    de._get_risk_range = lambda t, d=d: ({"buy_trade": d["rr_lo"],
                                          "sell_trade": d["rr_hi"]}
                                          if d["rr_lo"] is not None else {})
    de._get_mfr_latest = lambda t, d=d: {
        "price": d["price"], "range_low": d["mfr_lo"], "range_high": d["mfr_hi"],
        "trend_signal": d["trend"], "momentum_signal": d["momentum"],
        "hurst": 0.55,
        "call_wall_mfr": d["cw"], "put_wall_mfr": d["pw"], "zero_gamma": None,
    }
    de._get_yahoo_latest = lambda t, d=d: {"price": d["price"]}

    if side_flag is None:
        tools.active_slice.source_flags_for = lambda t: ["quad_aligned"]
    else:
        tools.active_slice.source_flags_for = lambda t, sf=side_flag: [sf, "quad_aligned"]

    FakeClient.haiku_reply = haiku_text
    res = de.decide_notifier(ticker, signal_origin="proactive_scan")

    fires = (res.get("conviction") in proactive_scanner.ACTIONABLE_CONVICTIONS
             and res.get("action") in proactive_scanner.ACTIONABLE_ACTIONS)
    print(f"  Haiku (stub):       {haiku_text}")
    print(f"  Side:               {res.get('side')}")
    print(f"  Zone:               {res['context_summary'].get('mfr_zone')}")
    print(f"  Final action:       {res.get('action')} "
          f"(conviction={res.get('conviction')} direction={res.get('direction')})")
    print(f"  Reasoning:          {res.get('reasoning')}")
    print(f"  Telegram fires?     {fires}")
    print()

de._get_risk_range = orig_rr
de._get_mfr_latest = orig_mfr
de._get_yahoo_latest = orig_yh
tools.active_slice.source_flags_for = orig_active_sf
