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
