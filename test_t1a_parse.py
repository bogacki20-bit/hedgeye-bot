"""Fixture tests for the Tier One Alpha parser — the fixture is a TRIMMED
COPY OF THE REAL OCR TEXT (doc_uploads id 4, 7/10 report), so these tests
break exactly when the report layout (or OCR behavior) changes.
    python test_t1a_parse.py
"""
from tools.t1a_parse import parse_t1a, fact_line

REAL_OCR = """Market Situation Report
July 10, 2026
SPX Gamma Exposure:
Systematic Flow Risk:
PV Band Risk/Reward:
Strategic Allocation:
Long
Long
Long
Risk On
Neutral
Neutral
Neutral
Neutral
Negative
Bearish
Short
Risk Off
SPX Key Levels And Strikes
Last Price: 7543.84
Upper PV Band: 7631D
Lower PV Band: 7415D
Upside Risk: 118%
Downside Risk: -185%
50D MA: 28%
GEX Throttle: 7.27
GEX Price: 7455.62
Implied Move: 128%
Resistance Strike: 7860D
Focal Strike: 7825
Support Strike: 7450
SPX Gamma Exposure:
Market makers are currently LONG GAMMA, indicating that lower volatility is expected.
Systematic Rebalancing:
Systematic Funds, including Vol Control, CTA, and Risk Parity strategies, are likely to increase their exposure to equities.
Strategic Allocation:
Historically, Neutral Risk regimes tend to be transitory periods characterized by mixed returns.
[CHART: Pie chart showing Strategic Allocation with segments for Core Position, 71.2%, Low Beta, and 28.8%]
Economic Event Calendar With Short-Dated Options Positioning
Date | Event | Estimate | Previous | Impact | Call IV | Put IV | SPX IV | Expected Move | P/C Vol | P/C(D)
2026-07-14 | Core Inflation Rate MoM | — | — | high | 12.98 | 12.23 | 12.55 | ~1.13% | 1.75 | 2.28
2026-07-15 | MBA Purchase Index | 189.5 | low | 12.93 | 12.27 | 12.6 | ~1.32% | 11 | 1.62
2026-07-16 | Philly Fed Employment | 7.8 | 8.1 | medium | 12.98 | 12.27 | 12.57 | ~1.67% | 0.72 | 1.81
"""

P = parse_t1a(REAL_OCR)


def test_regimes_come_from_prose_not_dials():
    # the dial selection doesn't survive OCR; prose states each regime
    assert P["gamma_regime"] == "positive"
    assert P["systematic_bias"] == "buyers"
    assert P["strategic_regime"] == "neutral"


def test_levels_survive_ocr_cleanly():
    assert P["last_price"] == 7543.84
    assert P["gex_flip"] == 7455.62
    assert P["gex_throttle"] == 7.27
    assert P["upper_pv"] == 7631        # trailing 'D' artifact stripped
    assert P["lower_pv"] == 7415
    assert P["support_strike"] == 7450
    assert P["focal_strike"] == 7825
    assert P["resistance_strike"] == 7860


def test_flip_distance_is_python_math_on_clean_prices():
    assert abs(P["flip_dist_pct"] - 1.17) < 0.02      # (7543.84-7455.62)/last


def test_ratio_fields_stored_raw_and_flagged_suspect():
    # OCR ate the decimals ('118%' ~ 1.18%): stored RAW, flagged, not fixed
    assert P["upside_risk"] == 118.0
    assert P["downside_risk"] == -185.0
    assert P["scale_suspect"] is True


def test_allocation_pie():
    assert P["core_pct"] == 71.2 and P["low_beta_pct"] == 28.8


def test_events_high_and_medium_only():
    ev = P["events"]
    assert [e["event"] for e in ev] == ["Core Inflation Rate MoM",
                                        "Philly Fed Employment"]
    assert ev[0]["impact"] == "high" and ev[0]["exp_move"] == "~1.13%"


def test_fact_line_render():
    row = dict(P, report_date="2026-07-10")
    line = fact_line(row)
    assert "gamma POSITIVE" in line
    assert "flip 7456 (+1.2%)" in line
    assert "throttle 7.27 (compression)" in line
    assert "systematics BUYERS" in line
    assert "strategic NEUTRAL" in line
    assert "Core Inflation Rate MoM 2026-07-14 (~1.13%)" in line
    assert "scale-suspect" in line


def test_missing_fields_are_none_never_guessed():
    p = parse_t1a("nothing useful here")
    assert p["gamma_regime"] is None and p["last_price"] is None
    assert p["events"] == [] and p["flip_dist_pct"] is None


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
