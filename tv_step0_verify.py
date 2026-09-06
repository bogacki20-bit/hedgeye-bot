"""tv_step0_verify.py — Step 0 of the TradingView MFR history backfill.

READ-ONLY. Verifies, before any ingest:
  1. Repaint check: CSV Range High/Low vs mfr_snapshots.range_high/low to 4 dp,
     for EVERY ticker that has live rows (SPY, UUP, ^VIX, USO, AAAU).
  2. Trend-derivation rule: which close/levels rule reproduces the feed's
     trend_signal (confusion matrices for three candidate rules).
  3. Timing: fetched_at of the overlapping live rows vs the bar's 09:30 ET
     open (upserts bump fetched_at, so "before open" is conservative evidence).
  4. Recompute fidelity, SPY: decel_streak/decelerating/price_down_3d
     (tools.volume_signal pure functions), 60d SPY-UUP return correlation
     (tools.relative_strength pearson/returns), Hurst
     (shadow_range.hurst_rs, 126-bar tail) — each recomputed from the TV bars
     and diffed against the live tables over the overlap.
  5. Volume provenance: TV volume vs mfr_snapshots.previous_day_volume and
     volume_snapshots.volume (is the TV feed consolidated tape?).

Run:  py tv_step0_verify.py
"""
from __future__ import annotations

import csv
import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from tools.volume_signal import (  # noqa: E402
    decel_streak, down_day_volume_slope, price_change,
    AVG_WINDOW, DOWN_LOOKBACK)
from tools.relative_strength import pearson, returns  # noqa: E402
from shadow_range import hurst_rs  # noqa: E402
import numpy as np  # noqa: E402

NY = ZoneInfo("America/New_York")

FILES = {  # csv basename -> mfr_snapshots ticker
    "BATS_SPY_1D.csv":  "SPY",
    "BATS_UUP_1D.csv":  "UUP",
    "TVC_VIX_1D.csv":   "^VIX",
    "BATS_USO_1D.csv":  "USO",
    "BATS_AAAU_1D.csv": "AAAU",
}
TV_DIR = REPO / "data" / "tradingview"

TREND_TO_TAG = {"trendBullish": 1, "trendNeutral": 0, "trendBearish": -1}


def _f(s: str):
    if s is None or s == "" or s.upper() == "NAN":
        return None
    return float(s)


def load_csv(path: Path):
    """[{date, open, high, low, close, bull, bear, rh, rl, lth, ltl, vol}],
    oldest first, warm-up rows (any NaN among the 6 indicator cols) counted
    and dropped from the front; a NaN AFTER warm-up is reported loudly."""
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        for rec in rd:
            ts = int(rec["time"])
            d = datetime.fromtimestamp(ts, tz=NY).date()
            rows.append({
                "date": d,
                "open": _f(rec["open"]), "high": _f(rec["high"]),
                "low": _f(rec["low"]), "close": _f(rec["close"]),
                "bull": _f(rec["Bullish Trend"]), "bear": _f(rec["Bearish Trend"]),
                "rh": _f(rec["Range High"]), "rl": _f(rec["Range Low"]),
                "lth": _f(rec["LT Range High"]), "ltl": _f(rec["LT Range Low"]),
                "vol": _f(rec.get("Volume")) if "Volume" in rec else None,
            })
    ind = ("bull", "bear", "rh", "rl", "lth", "ltl")
    n_warm = 0
    for r in rows:
        if any(r[k] is None for k in ind):
            n_warm += 1
        else:
            break
    body = rows[n_warm:]
    late_nan = [r["date"] for r in body if any(r[k] is None for k in ind)]
    return rows, body, n_warm, late_nan


def integrity(name, rows, body, n_warm, late_nan):
    print(f"\n### {name} — {len(rows)} rows, {rows[0]['date']} .. {rows[-1]['date']}, "
          f"warm-up skipped: {n_warm}")
    if late_nan:
        print(f"  !! NaN indicator values AFTER warm-up on {len(late_nan)} dates: "
              f"{late_nan[:10]}")
    dates = [r["date"] for r in rows]
    dups = [d for d, c in Counter(dates).items() if c > 1]
    if dups:
        print(f"  !! duplicate dates: {dups[:10]}")
    if dates != sorted(dates):
        print("  !! dates not monotonic")
    bad_rng = [r["date"] for r in body if not (r["rh"] > r["rl"])]
    bad_lt = [r["date"] for r in body if not (r["lth"] > r["ltl"])]
    bad_bb = [r["date"] for r in body if not (r["bull"] > r["bear"])]
    ins = [r["date"] for r in body
           if not (0.5 * r["close"] < r["rl"] < 2.0 * r["close"])]
    for label, bad in (("range_high<=range_low", bad_rng),
                       ("lt_high<=lt_low", bad_lt),
                       ("bull<=bear", bad_bb),
                       ("range_low insane vs close", ins)):
        if bad:
            print(f"  !! {label} on {len(bad)} dates: {bad[:10]}")
    if not (late_nan or dups or bad_rng or bad_lt or bad_bb or ins):
        print("  integrity: OK (monotonic, no dups, rh>rl, lth>ltl, bull>bear, sane)")


def live_rows(cur, ticker):
    cur.execute(
        """SELECT snapshot_date, range_low, range_high, trend_signal,
                  fetched_at, previous_day_volume
             FROM mfr_snapshots WHERE ticker=%s ORDER BY snapshot_date""",
        (ticker,))
    return {r[0]: {"rl": r[1], "rh": r[2], "trend": r[3],
                   "fetched": r[4], "pdv": r[5]} for r in cur.fetchall()}


ROUND_TOL = 5.0001e-4   # live feed stores 3-dp values; within half a milli = rounding


def repaint_check(ticker, body, live):
    """Rounding-aware: the live feed serves 3-dp values, the TV CSV full
    precision. |diff| <= 0.0005 = rounding, anything larger = REAL drift.
    For real drift, test the stale-feed hypothesis live[D] == csv[D-1]."""
    idx = {r["date"]: i for i, r in enumerate(body)}
    ov = [r for r in body if r["date"] in live
          and live[r["date"]]["rl"] is not None and live[r["date"]]["rh"] is not None]
    n_exact = n_round = 0
    real = []
    for r in ov:
        L = live[r["date"]]
        dlo = abs(r["rl"] - float(L["rl"]))
        dhi = abs(r["rh"] - float(L["rh"]))
        if round(r["rl"], 4) == round(float(L["rl"]), 4) and \
           round(r["rh"], 4) == round(float(L["rh"]), 4):
            n_exact += 1
        elif dlo <= ROUND_TOL and dhi <= ROUND_TOL:
            n_round += 1
        else:
            real.append((r, L, dlo, dhi))
    print(f"  repaint: {len(ov)} overlap dates — exact@4dp {n_exact}, "
          f"3dp-rounding-only {n_round}, REAL drift {len(real)}")
    for r, L, dlo, dhi in real[:20]:
        d = r["date"]
        i = idx[d]
        prev = body[i - 1] if i > 0 else None
        stale = (prev is not None
                 and abs(prev["rl"] - float(L["rl"])) <= ROUND_TOL
                 and abs(prev["rh"] - float(L["rh"])) <= ROUND_TOL)
        pct = max(dlo, dhi) / r["close"] * 100
        print(f"    !! {d}: csv {r['rl']:.4f}/{r['rh']:.4f}  live "
              f"{float(L['rl']):.4f}/{float(L['rh']):.4f}  (max diff {pct:.3f}% of close)"
              f"{'  <- live row = csv PREVIOUS bar (stale feed day)' if stale else ''}")
    # evidence subset: rows whose LAST fetch was before the 09:30 open —
    # for these the value provably existed pre-open; do they match?
    pre = [(r, L) for r, L, *_ in [(r, live[r["date"]], 0, 0) for r in ov]
           if L["fetched"].astimezone(NY)
              < datetime(r["date"].year, r["date"].month, r["date"].day, 9, 30, tzinfo=NY)]
    pre_bad = sum(1 for r, L in pre
                  if abs(r["rl"] - float(L["rl"])) > ROUND_TOL
                  or abs(r["rh"] - float(L["rh"])) > ROUND_TOL)
    print(f"  pre-open-fetch subset: {len(pre)} rows fetched before D 09:30, "
          f"{pre_bad} beyond rounding tolerance")
    return len(ov), len(real)


def weekend_check(ticker, body, cur):
    """Live rows dated Sat/Sun: which TV bar do they equal — the previous
    trading day's or the next? Tells us what a weekend-dated feed row is."""
    cur.execute(
        """SELECT snapshot_date, range_low, range_high FROM mfr_snapshots
            WHERE ticker=%s AND extract(isodow FROM snapshot_date) IN (6,7)
              AND range_low IS NOT NULL ORDER BY snapshot_date""", (ticker,))
    wk = cur.fetchall()
    dates = [r["date"] for r in body]
    n_prev = n_next = n_neither = 0
    for d, rl, rh in wk:
        prev = next((r for r in reversed(body) if r["date"] < d), None)
        nxt = next((r for r in body if r["date"] > d), None)
        rl, rh = float(rl), float(rh)
        if prev and abs(prev["rl"] - rl) <= ROUND_TOL and abs(prev["rh"] - rh) <= ROUND_TOL:
            n_prev += 1
        elif nxt and abs(nxt["rl"] - rl) <= ROUND_TOL and abs(nxt["rh"] - rh) <= ROUND_TOL:
            n_next += 1
        else:
            n_neither += 1
    if wk:
        print(f"  weekend-dated live rows: {len(wk)} — equal PREV TV bar: {n_prev}, "
              f"equal NEXT TV bar: {n_next}, neither: {n_neither}")


def trend_check(ticker, body, live):
    """Confusion matrices for three candidate derivation rules."""
    by_date = {r["date"]: r for r in body}
    idx = {r["date"]: i for i, r in enumerate(body)}

    def tag(close, bull, bear):
        return 1 if close > bull else (-1 if close < bear else 0)

    rules = {
        "R1 close[D]   vs levels[D]": lambda r, p: tag(r["close"], r["bull"], r["bear"]),
        "R2 close[D-1] vs levels[D]": lambda r, p: tag(p["close"], r["bull"], r["bear"]) if p else None,
        "R3 close[D-1] vs levels[D-1]": lambda r, p: tag(p["close"], p["bull"], p["bear"]) if p else None,
    }
    ov = [d for d in by_date
          if d in live and live[d]["trend"] in TREND_TO_TAG]
    ov.sort()
    print(f"  trend rule check over {len(ov)} overlap dates "
          f"(feed values: {Counter(live[d]['trend'] for d in ov)})")
    for label, fn in rules.items():
        conf = Counter()
        n_ok = n = 0
        for d in ov:
            i = idx[d]
            prev = body[i - 1] if i > 0 else None
            got = fn(by_date[d], prev)
            if got is None:
                continue
            want = TREND_TO_TAG[live[d]["trend"]]
            conf[(want, got)] += 1
            n += 1
            n_ok += (got == want)
        acc = 100.0 * n_ok / n if n else 0.0
        off = {k: v for k, v in sorted(conf.items()) if k[0] != k[1]}
        print(f"    {label}: {n_ok}/{n} = {acc:.1f}%   off-diagonal {off if off else '{}'}")


def timing_check(ticker, body, live):
    dates = [r["date"] for r in body if r["date"] in live]
    before_prev_day = before_open = after_open = 0
    for d in dates:
        f = live[d]["fetched"].astimezone(NY)
        open_ts = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)
        if f.date() < d:
            before_prev_day += 1
        elif f < open_ts:
            before_open += 1
        else:
            after_open += 1
    n = len(dates)
    print(f"  timing (LAST fetched_at; upserts only push it later): n={n}  "
          f"fetched on D-1 or earlier: {before_prev_day}, on D pre-09:30: {before_open}, "
          f"on D post-09:30: {after_open}")
    late = [(d, live[d]["fetched"].astimezone(NY).strftime("%H:%M"))
            for d in dates
            if live[d]["fetched"].astimezone(NY)
               >= datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)]
    if late:
        hrs = Counter(t for _, t in late)
        print(f"    post-open fetch times: {dict(sorted(hrs.items()))}")


def volume_check(ticker, body, live):
    if all(r["vol"] is None for r in body):
        print("  volume: no volume column (index)")
        return
    ratios = []
    for i in range(1, len(body)):
        d, prev = body[i]["date"], body[i - 1]
        L = live.get(d)
        if L and L["pdv"] and prev["vol"]:
            ratios.append(prev["vol"] / float(L["pdv"]))
    if ratios:
        ratios.sort()
        med = ratios[len(ratios) // 2]
        print(f"  volume: TV vol[D-1] / mfr previous_day_volume[D] over "
              f"{len(ratios)} pairs: median {med:.4f}, "
              f"min {ratios[0]:.4f}, max {ratios[-1]:.4f}")
    else:
        print("  volume: no overlapping previous_day_volume to compare")


# ── SPY recompute fidelity ──────────────────────────────────────────────────

def recompute_decel(cur, spy_body):
    cur.execute(
        """SELECT snapshot_date, volume, decel_streak, decelerating,
                  price_down_3d
             FROM volume_snapshots WHERE ticker='SPY' ORDER BY snapshot_date""")
    livev = {r[0]: r[1:] for r in cur.fetchall()}
    idx = {r["date"]: i for i, r in enumerate(spy_body)}
    lookback = AVG_WINDOW + DOWN_LOOKBACK + 15
    n = n_streak = n_decel = n_down3 = 0
    vol_ratios = []
    diffs = []
    for d, (lvol, lstreak, ldecel, ldown3) in livev.items():
        i = idx.get(d)
        if i is None or i + 1 < lookback:
            continue
        closes = [r["close"] for r in spy_body[i + 1 - lookback: i + 1]]
        vols = [r["vol"] for r in spy_body[i + 1 - lookback: i + 1]]
        streak = min(decel_streak(closes, vols), 7)
        slope, _ = down_day_volume_slope(closes, vols)
        decel = slope is not None and slope < 0
        pchg = price_change(closes)
        down3 = pchg is not None and pchg < 0
        n += 1
        n_streak += (streak == min(lstreak or 0, 7))
        n_decel += (decel == ldecel)
        n_down3 += (down3 == ldown3)
        if lvol:
            vol_ratios.append(spy_body[i]["vol"] / float(lvol))
        if streak != min(lstreak or 0, 7) or decel != ldecel or down3 != ldown3:
            diffs.append((d, streak, lstreak, decel, ldecel, down3, ldown3))
    print(f"\n### SPY decel recompute vs volume_snapshots ({n} overlap rows)")
    print(f"  decel_streak match: {n_streak}/{n}   decelerating match: {n_decel}/{n}"
          f"   price_down_3d match: {n_down3}/{n}")
    if vol_ratios:
        vol_ratios.sort()
        print(f"  TV volume / live(yfinance) volume, same date: median "
              f"{vol_ratios[len(vol_ratios)//2]:.4f}, min {vol_ratios[0]:.4f}, "
              f"max {vol_ratios[-1]:.4f}")
    for d, s, ls, de, lde, d3, ld3 in diffs[:15]:
        print(f"    diff {d}: streak tv={s} live={ls}  decel tv={de} live={lde}  "
              f"down3 tv={d3} live={ld3}")


def recompute_corr(cur, spy_body, uup_body):
    cur.execute(
        """SELECT snapshot_date, correlation FROM correlation_snapshots
            WHERE ticker_a='UUP' AND ticker_b='SPY' AND window_days=60
            ORDER BY snapshot_date""")
    livec = {r[0]: float(r[1]) for r in cur.fetchall()}
    sidx = {r["date"]: i for i, r in enumerate(spy_body)}
    uidx = {r["date"]: i for i, r in enumerate(uup_body)}
    diffs, n = [], 0
    for d, lv in livec.items():
        si, ui = sidx.get(d), uidx.get(d)
        if si is None or ui is None or si < 61 or ui < 61:
            continue
        rs = returns([r["close"] for r in spy_body[: si + 1]])
        ru = returns([r["close"] for r in uup_body[: ui + 1]])
        c = pearson(ru[-60:], rs[-60:])
        if c is None:
            continue
        n += 1
        diffs.append((d, abs(round(c, 5) - lv), round(c, 5), lv))
    absd = sorted(x[1] for x in diffs)
    print(f"\n### SPY-UUP corr60 recompute vs correlation_snapshots ({n} overlap rows)")
    if absd:
        print(f"  |diff|: median {absd[len(absd)//2]:.5f}, max {absd[-1]:.5f}; "
              f"within 0.01: {sum(x <= 0.01 for x in absd)}/{n}, "
              f"within 0.05: {sum(x <= 0.05 for x in absd)}/{n}")
        for d, ad, c, lv in sorted(diffs, key=lambda x: -x[1])[:5]:
            print(f"    worst {d}: tv {c:+.5f}  live {lv:+.5f}")


def recompute_hurst(cur, spy_body):
    cur.execute(
        """SELECT snapshot_date, shadow_hurst, bars FROM shadow_snapshots
            WHERE ticker='SPY' AND status='ok' AND shadow_hurst IS NOT NULL
            ORDER BY snapshot_date""")
    liveh = {r[0]: (float(r[1]), r[2]) for r in cur.fetchall()}
    cur.execute("SELECT snapshot_date, hurst FROM mfr_snapshots "
                "WHERE ticker='SPY' AND hurst IS NOT NULL")
    feedh = {r[0]: float(r[1]) for r in cur.fetchall()}
    idx = {r["date"]: i for i, r in enumerate(spy_body)}
    diffs_shadow, diffs_feed = [], []
    for d, i in idx.items():
        if i + 1 < 126:
            continue
        closes = np.array([r["close"] for r in spy_body[i + 1 - 126: i + 1]])
        h = hurst_rs(closes)
        if math.isnan(h):
            continue
        if d in liveh:
            diffs_shadow.append(abs(h - liveh[d][0]))
        if d in feedh:
            diffs_feed.append(abs(h - feedh[d]))
    print(f"\n### SPY Hurst recompute (shadow_range.hurst_rs, 126-bar tail)")
    for label, ds in (("vs shadow_snapshots.shadow_hurst", diffs_shadow),
                      ("vs mfr_snapshots.hurst (MFR feed's own)", diffs_feed)):
        if ds:
            ds.sort()
            print(f"  {label}: n={len(ds)}, |diff| median {ds[len(ds)//2]:.4f}, "
                  f"max {ds[-1]:.4f}, within 0.01: {sum(x <= 0.01 for x in ds)}, "
                  f"within 0.05: {sum(x <= 0.05 for x in ds)}")
        else:
            print(f"  {label}: no overlap")


def main():
    parsed = {}
    for fname, ticker in FILES.items():
        rows, body, n_warm, late_nan = load_csv(TV_DIR / fname)
        parsed[ticker] = body
        integrity(f"{fname} -> {ticker}", rows, body, n_warm, late_nan)

    total_mism = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for fname, ticker in FILES.items():
            body = parsed[ticker]
            live = live_rows(cur, ticker)
            print(f"\n### {ticker} vs mfr_snapshots ({len(live)} live rows)")
            _, m = repaint_check(ticker, body, live)
            total_mism += m
            weekend_check(ticker, body, cur)
            trend_check(ticker, body, live)
            timing_check(ticker, body, live)
            volume_check(ticker, body, live)

        recompute_decel(cur, parsed["SPY"])
        recompute_corr(cur, parsed["SPY"], parsed["UUP"])
        recompute_hurst(cur, parsed["SPY"])

    print(f"\n=== STEP 0 SUMMARY: repaint mismatches across all tickers: {total_mism} ===")
    return 0 if total_mism == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
