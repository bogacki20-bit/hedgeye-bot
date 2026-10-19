"""Fixture tests for tools/ai_short_watch (pure, no DB).
    python test_ai_short_watch.py
"""
from tools.ai_short_watch import in_zone, detect


def row(t, price, rp, trade, trend_dir="BEARISH", **kw):
    r = {"ticker": t, "layer": "neocloud", "severity_pct": -89, "price": price,
         "rp": rp, "range_low": 58.5, "range_high": 74.59, "trend_dir": trend_dir,
         "tv_trade": trade, "tv_trend": 94.13, "ss_side": "short list",
         "held_short": False}
    r.update(kw)
    return r


def test_zone_rules():
    # CRWV 10/16: rp 0.18, 28% under TRADE -> not a bounce
    assert in_zone(row("CRWV", 61.39, 0.18, 78.71)) == (False, "")
    z, why = in_zone(row("CRWV", 72.0, 0.84, 78.71))
    assert z and "rp 0.84" in why
    z, why = in_zone(row("ORCL", 125.0, 0.30, 125.39))     # within 1% of TRADE
    assert z and "TRADE" in why
    assert not in_zone(row("CEG", 300.0, 0.95, 269.0, trend_dir="NEUTRAL"))[0]
    assert not in_zone(row("X", 10.0, None, None))[0]


def test_fires_only_on_entering_zone():
    r = [row("CRWV", 72.0, 0.84, 78.71)]
    a, s = detect(r, {})                      # first sight seeds
    assert not a and s["CRWV"] is True
    a, _ = detect(r, {"CRWV": False})         # entered -> fire
    assert len(a) == 1 and "AI SHORT WATCH CRWV" in a[0]["line"]
    assert "-89%" in a[0]["line"] and "Individual" in a[0]["line"]
    a, _ = detect(r, {"CRWV": True})          # standing -> silent
    assert not a
    a, _ = detect([row("CRWV", 72.0, 0.84, 78.71, held_short=True)], {"CRWV": False})
    assert "ALREADY short" in a[0]["line"]


if __name__ == "__main__":
    import sys, inspect
    fails = 0
    for name, fn in sorted(inspect.getmembers(sys.modules["__main__"], inspect.isfunction)):
        if name.startswith("test_"):
            try:
                fn(); print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1; print(f"  FAIL  {name}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
