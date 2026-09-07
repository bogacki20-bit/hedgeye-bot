"""ml/build_features.py — Round-2 Phase A Step 1: build ml_features.

Ports EXACTLY the TrendSpider feature spec (ml/spec/MFR_CORE_ALL.js +
ml/spec/MFR_SPY_CORE_v3_and_USO.md). Pooled-model clamps: bulldist +/-0.5,
hi_d3/lo_d3 +/-0.2. VIX context uses the ^VIX INDEX close (px_daily), not the
range-midpoint proxy the JS was forced into. decel comes from
tools.volume_signal's own functions, not a re-port.

No lookahead: every input at bar D carries known_at <= D 16:00 ET —
ranges/levels/round-2 features dated D publish the prior evening (Step 0
timing doctrine), close[D] is known at the close. known_at = D 16:00 ET.

Union rule (same as trendspider_export.py): range_low/high come from
mfr_snapshots for dates >= the ticker's first live range date, TV history
before (source='live' / 'tv'). TLT is TV-indicator end to end
(source='tv_unverified', the 2026-09-06 waiver). Everything else
(LT range, bulldist, hurst64/256, trend/trade levels, flags, vixfix,
volatility) is TV-history throughout — no live table carries them.

    py ml/build_features.py            # build + upsert + validation report
    py ml/build_features.py --dry-run  # build + validation only
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402
from tools.relative_strength import pearson  # noqa: E402
from tools.volume_signal import (decel_streak, down_day_volume_slope,  # noqa: E402
                                 price_change)

NY = ZoneInfo("America/New_York")
from ml.universe import ASSET_CLASS, TICKERS  # noqa: E402

FEATURES = [
    "rp", "rp_dev20", "bulldist", "rng_width", "ltrp", "hurst64", "hurst256",
    "trend_dist", "trade_dist", "above_trend", "vixfix", "volatility",
    "buy", "mega_buy", "sell", "mega_sell",
    "rp_d3", "bulldist_d3", "hi_d3", "lo_d3", "trend_dist_d3", "hurst64_d5",
    "rng_width_d5", "decel_streak", "distribution",
    "vix_level", "vix_bucket", "vix_roc5", "hyg_roc10", "uup_rp", "uup_rp_d3",
    "corr30_spy", "corr30_uup", "usd_pressure",
]


def clamp(s, lo, hi):
    return s.clip(lower=lo, upper=hi)


def load_inputs():
    with db_pg.get_conn() as conn:
        tv = pd.read_sql(
            "SELECT ticker, bar_date, range_low, range_high, lt_range_low, "
            "lt_range_high FROM tv_mfr_history WHERE ticker = ANY(%s)",
            conn, params=([t for t in TICKERS],))
        live = pd.read_sql(
            "SELECT ticker, snapshot_date AS bar_date, range_low, range_high "
            "FROM mfr_snapshots WHERE ticker = ANY(%s) "
            "AND range_low IS NOT NULL AND range_high IS NOT NULL "
            "AND extract(isodow FROM snapshot_date) < 6",
            conn, params=([t for t in TICKERS],))
        feat = pd.read_sql(
            "SELECT ticker, bar_date, feature, value FROM tv_features_history "
            "WHERE ticker = ANY(%s) AND feature = ANY(%s)",
            conn, params=([t for t in TICKERS],
                          ["hurst64", "hurst256", "trend_lvl", "trade_lvl",
                           "buy", "mega_buy", "sell", "mega_sell", "vixfix",
                           "volatility"]))
        px = pd.read_sql(
            "SELECT ticker, bar_date, c, v FROM px_daily "
            "WHERE ticker = ANY(%s)", conn,
            params=([t for t in TICKERS] + ["^VIX", "HYG"],))
    for df in (tv, live, feat, px):
        df["bar_date"] = pd.to_datetime(df["bar_date"])
        for col in df.columns:
            if col not in ("ticker", "bar_date", "feature"):
                df[col] = df[col].astype(float)
    return tv, live, feat, px


def per_ticker_frame(t, tv, live, feat, px):
    """One DataFrame indexed by bar_date with raw inputs for ticker t.
    Bars = the ticker's px_daily (TV CSV) dates — the feature calendar."""
    bars = px[px.ticker == t].set_index("bar_date").sort_index()
    f = pd.DataFrame(index=bars.index)
    f["close"], f["volume"] = bars["c"], bars["v"]

    tvr = tv[tv.ticker == t].set_index("bar_date").sort_index()
    f["lo_tv"], f["hi_tv"] = tvr["range_low"], tvr["range_high"]
    f["ltlo"], f["lthi"] = tvr["lt_range_low"], tvr["lt_range_high"]

    lv = live[live.ticker == t].set_index("bar_date").sort_index()
    boundary = lv.index.min() if len(lv) and t != "TLT" else None
    f["lo"], f["hi"] = f["lo_tv"], f["hi_tv"]
    f["source"] = "tv_unverified" if t == "TLT" else "tv"
    if boundary is not None:
        use = f.index >= boundary
        f.loc[use, "lo"] = lv["range_low"].reindex(f.index)[use]
        f.loc[use, "hi"] = lv["range_high"].reindex(f.index)[use]
        f.loc[use, "source"] = "live"

    fw = (feat[feat.ticker == t]
          .pivot_table(index="bar_date", columns="feature", values="value"))
    for c in ("hurst64", "hurst256", "trend_lvl", "trade_lvl", "buy",
              "mega_buy", "sell", "mega_sell", "vixfix", "volatility"):
        f[c] = fw[c] if c in fw else np.nan
    return f


def compute(f):
    """All per-ticker features per the JS spec. f: raw frame from
    per_ticker_frame. Returns DataFrame of FEATURES minus context/cross."""
    o = pd.DataFrame(index=f.index)
    c, lo, hi = f["close"], f["lo"], f["hi"]
    o["rp"] = clamp((c - lo) / (hi - lo).where((hi - lo) > 0), -0.5, 1.5)
    o["rp_dev20"] = o["rp"] - o["rp"].rolling(20, min_periods=1).mean()
    o["bulldist"] = np.nan  # placeholder; bulldist is a stored round-1 feature
    o["rng_width"] = ((hi - lo) / c).where(c > 0)
    o["ltrp"] = clamp((c - f["ltlo"]) / (f["lthi"] - f["ltlo"])
                      .where((f["lthi"] - f["ltlo"]) > 0), -0.5, 1.5)
    o["hurst64"] = clamp(f["hurst64"], 0, 1)
    o["hurst256"] = clamp(f["hurst256"], 0, 1)
    o["trend_dist"] = clamp(((c - f["trend_lvl"]) / c).where(c > 0), -0.5, 0.5)
    o["trade_dist"] = clamp(((c - f["trade_lvl"]) / c).where(c > 0), -0.5, 0.5)
    o["above_trend"] = (o["trend_dist"] > 0).astype(float).where(o["trend_dist"].notna())
    o["vixfix"] = f["vixfix"]
    o["volatility"] = f["volatility"]
    for flag in ("buy", "mega_buy", "sell", "mega_sell"):
        o[flag] = f[flag]
    o["rp_d3"] = o["rp"] - o["rp"].shift(3)
    o["bulldist_d3"] = np.nan
    o["hi_d3"] = clamp(hi / hi.shift(3) - 1, -0.2, 0.2)
    o["lo_d3"] = clamp(lo / lo.shift(3) - 1, -0.2, 0.2)
    o["trend_dist_d3"] = o["trend_dist"] - o["trend_dist"].shift(3)
    o["hurst64_d5"] = o["hurst64"] - o["hurst64"].shift(5)
    o["rng_width_d5"] = o["rng_width"] - o["rng_width"].shift(5)
    # decel — the live module's own functions, bar by bar
    closes, vols = f["close"].tolist(), f["volume"].tolist()
    ds, dist = [], []
    for i in range(len(closes)):
        cs, vs = closes[: i + 1], vols[: i + 1]
        if i < 21 or any(x is None or x != x for x in vs[-13:]):
            ds.append(np.nan)
            dist.append(np.nan)
            continue
        streak = min(decel_streak(cs, vs), 7)
        slope, _ = down_day_volume_slope(cs, vs)
        decel = slope is not None and slope < 0
        pchg = price_change(cs)
        down3 = pchg is not None and pchg < 0
        ds.append(float(streak))
        dist.append(1.0 if (down3 and not decel) else 0.0)
    o["decel_streak"], o["distribution"] = ds, dist
    o["source"] = f["source"]
    return o


def add_bulldist(o, t):
    """bulldist is the stored round-1 TV feature (feed-definition level),
    clamped +/-0.5 per the pooled spec; d3 on the clamped series."""
    with db_pg.get_conn() as conn:
        bd = pd.read_sql(
            "SELECT bar_date, value FROM tv_features_history "
            "WHERE ticker=%s AND feature='bull_dist'", conn, params=(t,))
    bd["bar_date"] = pd.to_datetime(bd["bar_date"])
    s = clamp(bd.set_index("bar_date")["value"].astype(float), -0.5, 0.5)
    o["bulldist"] = s.reindex(o.index)
    o["bulldist_d3"] = o["bulldist"] - o["bulldist"].shift(3)
    return o


def trailing_corr30(ret_a: pd.Series, ret_b: pd.Series, dates) -> list:
    """Pearson of trailing-30-session date-aligned returns, >=20 pairs, using
    tools.relative_strength.pearson (the live math)."""
    out = []
    joined = pd.concat({"a": ret_a, "b": ret_b}, axis=1, sort=True)
    for d in dates:
        w = joined.loc[:d].tail(30).dropna()
        if len(w) < 20:
            out.append(np.nan)
        else:
            r = pearson(w["a"].tolist(), w["b"].tolist())
            out.append(np.nan if r is None else r)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tv, live, feat, px = load_inputs()
    frames = {t: compute(per_ticker_frame(t, tv, live, feat, px))
              for t in TICKERS}
    for t in TICKERS:
        frames[t] = add_bulldist(frames[t], t)

    # context series (by date)
    vix = px[px.ticker == "^VIX"].set_index("bar_date")["c"].sort_index()
    hyg = px[px.ticker == "HYG"].set_index("bar_date")["c"].sort_index()
    vix_level = clamp(vix, 5, 90)
    vix_bucket = pd.Series(np.where(vix < 20, 0, np.where(vix <= 30, 1, 2)),
                           index=vix.index, dtype=float).where(vix.notna())
    vix_roc5 = clamp(vix / vix.shift(5) - 1, -0.6, 1.5)
    hyg_roc10 = clamp(hyg / hyg.shift(10) - 1, -0.15, 0.15)
    uup_rp = frames["UUP"]["rp"]
    uup_rp_d3 = uup_rp - uup_rp.shift(3)

    closes = {t: px[px.ticker == t].set_index("bar_date")["c"].sort_index()
              for t in TICKERS}
    rets = {t: closes[t].pct_change() for t in TICKERS}

    all_rows = []
    for t in TICKERS:
        o = frames[t]
        for name, s in (("vix_level", vix_level), ("vix_bucket", vix_bucket),
                        ("vix_roc5", vix_roc5), ("hyg_roc10", hyg_roc10),
                        ("uup_rp", uup_rp), ("uup_rp_d3", uup_rp_d3)):
            o[name] = s.reindex(o.index)
        o["corr30_spy"] = trailing_corr30(rets[t], rets["SPY"], o.index)
        o["corr30_uup"] = trailing_corr30(rets[t], rets["UUP"], o.index)
        o["usd_pressure"] = -o["corr30_uup"] * (o["uup_rp"] - 0.5)

        for d, row in o.iterrows():
            ka = datetime(d.year, d.month, d.day, 16, 0, tzinfo=NY)
            vals = [None if (row[k] != row[k]) else float(row[k])
                    for k in FEATURES]
            all_rows.append((t, d.date(), ka, row["source"],
                             ASSET_CLASS[t], *vals))

    # ── validation report ──
    print(f"{'ticker':<7} {'rows':>5}  span")
    for t in TICKERS:
        o = frames[t]
        print(f"{t:<7} {len(o):>5}  {o.index.min().date()} .. {o.index.max().date()}"
              f"  source: {dict(o['source'].value_counts())}")
    print("\nNaN share / min / max per feature (pooled):")
    pooled = pd.concat([frames[t][FEATURES] for t in TICKERS])
    for k in FEATURES:
        s = pooled[k]
        print(f"  {k:<14} nan={s.isna().mean()*100:5.1f}%  "
              f"min={s.min():.4f}  max={s.max():.4f}")

    print("\nSpot-checks (logged TrendSpider values in parentheses):")
    checks = [("USO", "2026-09-04", {"rp": 0.717, "hurst64": 0.75,
                                     "hurst256": 0.66, "trend_dist": 0.103}),
              ("TLT", "2026-09-04", {"rp": 0.445, "hurst64": 0.70,
                                     "hurst256": 0.61, "trend_dist": -0.008}),
              ("SPY", "2026-09-04", {})]
    for t, d, expect in checks:
        row = frames[t].loc[pd.Timestamp(d)]
        got = {k: round(float(row[k]), 3)
               for k in ("rp", "hurst64", "hurst256", "trend_dist")}
        exp = "".join(f" ({k} logged {v})" for k, v in expect.items())
        print(f"  {t} {d}: {got}{exp}")

    if args.dry_run:
        print(f"\n[dry-run] {len(all_rows)} rows, no writes")
        return 0
    cols = ("ticker, bar_date, known_at, source, asset_class, "
            + ", ".join(FEATURES))
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(all_rows), 500):
            execute_values(
                cur,
                f"INSERT INTO ml_features ({cols}) VALUES %s "
                f"ON CONFLICT (ticker, bar_date) DO UPDATE SET "
                + ", ".join(f"{k}=EXCLUDED.{k}" for k in
                            ["known_at", "source", "asset_class"] + FEATURES)
                + ", built_at=now()",
                all_rows[i:i + 500], page_size=500)
            conn.commit()
        cur.execute("SELECT count(*) FROM ml_features")
        print(f"\nml_features now holds {cur.fetchone()[0]} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
