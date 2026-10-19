"""Fixture tests for tools/book_alerts.detect (pure, no DB) — CROSSING
semantics: dips/rips fire only when the retreat crosses the threshold vs the
previous cycle; unknown prior state seeds silently.
    python test_book_alerts.py
"""
from tools.book_alerts import detect


def row(t, side, trend, rp, hi=None, lo=None, wrap=None):
    return {"ticker": t, "side": side, "trend_dir": trend, "rp_now": rp,
            "rp_5d_max": hi, "rp_5d_min": lo, "wrap": wrap}


def test_dip_fires_only_on_crossing():
    r = [row("XLV", "long", "BULLISH", 0.63, hi=0.895)]   # retreat 0.265
    # first sighting (no prior state) -> seed silently
    a, ret = detect(r, delta=0.25, prev_retreats={})
    assert not a and abs(ret["XLV"] - 0.265) < 1e-6
    # prior retreat below threshold -> crossing -> fire
    a, _ = detect(r, delta=0.25, prev_retreats={"XLV": 0.19})
    assert len(a) == 1 and a[0]["type"] == "dip" and "0.27" in a[0]["line"]
    # already in zone last cycle -> no re-fire
    a, _ = detect(r, delta=0.25, prev_retreats={"XLV": 0.265})
    assert not a


def test_no_flood_on_standing_dips():
    rows = [row(t, "long", "BULLISH", 0.30, hi=0.80) for t in
            ("A", "B", "C", "D", "E")]                    # all retreat 0.50
    a, ret = detect(rows, delta=0.25, prev_retreats={})   # fresh deploy
    assert not a and len(ret) == 5                        # seed, no flood


def test_dip_needs_trend_intact():
    a, ret = detect([row("SHY", "long", "BEARISH", 0.30, hi=0.80)],
                    delta=0.25, prev_retreats={"SHY": 0.0})
    assert not a and "SHY" not in ret     # trend against: not even tracked


def test_rip_mirror_for_shorts():
    r = [row("EIS", "short", "BEARISH", 0.72, lo=0.40)]   # rise 0.32
    a, _ = detect(r, delta=0.25, prev_retreats={"EIS": 0.10})
    assert len(a) == 1 and a[0]["type"] == "rip"
    a, _ = detect(r, delta=0.25, prev_retreats={"EIS": 0.30})
    assert not a                                          # already in zone


def test_trend_flip_fires_only_on_transition():
    r = [row("SBIT", "short", "BULLISH", 0.5, wrap="u:BTCUSD↯inv")]
    a, _ = detect(r, prev_trends={})
    assert not a                                          # first sighting
    a, _ = detect(r, prev_trends={"SBIT": "BEARISH"})
    assert len(a) == 1 and a[0]["type"] == "trend_flip"
    assert "BTCUSD" in a[0]["line"] and "SHORT" in a[0]["line"]
    a, _ = detect(r, prev_trends={"SBIT": "BULLISH"})
    assert not a                                          # standing condition


def test_long_flip_to_bearish():
    a, _ = detect([row("XLU", "long", "BEARISH", 0.5)],
                  prev_trends={"XLU": "BULLISH"})
    assert len(a) == 1 and a[0]["type"] == "trend_flip"


def test_missing_rp_never_crashes_or_guesses():
    a, ret = detect([row("DARK1", "long", "BULLISH", None, hi=0.9)])
    assert not a and not ret


# ── TRADE/TREND line crosses (2026-10-17) ──
from tools.book_alerts import level_zone, detect_levels, close_summary


def lrow(t, side, price, trade, trend, basis="bull"):
    return {"ticker": t, "side": side, "price": price, "tv_trade": trade,
            "tv_trend": trend, "tv_basis": basis, "tv_bar_date": "2026-10-15"}


def test_level_zone_long_and_short():
    # NET 10/16 close: 296.30 vs trade 334.02 / trend 299.18 -> trend break
    assert level_zone("long", 296.30, 334.02, 299.18, "bull") == "trend_break"
    assert level_zone("long", 310.0, 334.02, 299.18, "bull") == "trade_break"
    assert level_zone("long", 340.0, 334.02, 299.18, "bull") == "ok"
    # long in a bearish-trend name above its line: caution, never ok
    assert level_zone("long", 354.72, 321.72, 353.63, "bear-mirror") == "trade_break"
    assert level_zone("long", 350.0, 321.72, 353.63, "bear-mirror") == "trend_break"
    # shorts mirror: EWW above a bull-basis trend is against the short
    assert level_zone("short", 74.94, 73.21, 72.82, "bull") == "trend_break"
    assert level_zone("short", 37.9, 38.41, 41.59, "bear-mirror") == "ok"
    assert level_zone("short", 39.0, 38.41, 41.59, "bear-mirror") == "trade_break"
    assert level_zone("long", None, 1, 1, "bull") is None
    assert level_zone("long", 10, 11, None, "bull") is None


def test_level_cross_fires_only_on_transition():
    r = [lrow("NET", "long", 296.30, 334.02, 299.18)]
    a, z = detect_levels(r, {})                       # first sight seeds
    assert not a and z["NET"] == "trend_break"
    a, _ = detect_levels(r, {"NET": "trade_break"})   # worsened -> fire
    assert len(a) == 1 and a[0]["type"] == "trend_break" and "stop" in a[0]["line"]
    assert "intraday" in a[0]["line"]
    a, _ = detect_levels(r, {"NET": "trend_break"})   # standing -> silent
    assert not a
    a, _ = detect_levels(r, {"NET": "ok"}, at_close=True)
    assert a[0]["type"] == "trend_break" and "on the close" in a[0]["line"]


def test_level_trade_break_and_reclaim():
    a, _ = detect_levels([lrow("QQQ", "long", 716.0, 735.67, 704.03)], {"QQQ": "ok"})
    assert len(a) == 1 and a[0]["type"] == "trade_break" and "minimum" in a[0]["line"]
    a, _ = detect_levels([lrow("QQQ", "long", 740.0, 735.67, 704.03)], {"QQQ": "trend_break"})
    assert len(a) == 1 and a[0]["type"] == "trend_reclaim"
    # improving trend_break -> trade_break is not an alert
    a, _ = detect_levels([lrow("QQQ", "long", 710.0, 735.67, 704.03)], {"QQQ": "trend_break"})
    assert not a


def test_close_summary():
    rows = [lrow("NET", "long", 296.30, 334.02, 299.18),
            lrow("QQQ", "long", 709.8, 735.67, 704.03),
            lrow("EWW", "short", 74.94, 73.21, 72.82),
            lrow("XLV", "long", 177.0, 170.0, 160.0)]
    s = close_summary(rows)
    assert "NET 296.30 vs trend 299.18" in s and "EWW (S)" in s
    assert "QQQ" in s.split("Below TRADE")[1] and "XLV" not in s
    assert close_summary([rows[3]]) is None


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
