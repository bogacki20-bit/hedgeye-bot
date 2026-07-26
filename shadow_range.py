"""
shadow_range.py — Shadow Fractal Range Engine (MFR replacement / validator)
============================================================================
Reference implementation for the trading bot. Computes, from OHLC price
history alone:

    - Hurst exponent (rescaled-range / R-S analysis)
    - Realized volatility (EWMA close-to-close + Parkinson OHLC estimator)
    - A vol-adjusted, Hurst-skewed probable range  [low - high]
    - rp (range position, 0-1; >1 broken above, <0 broken below)
    - Trend state (BULL / BEAR / NEUT) and momentum

Plus a calibration harness that fits the tunable parameters against an
archive of known-good MFR (or Hedgeye) ranges, and a validator that flags
live feed values diverging from the shadow calculation.

Dependencies: numpy, pandas (scipy optional, not required).
Input convention: pandas DataFrame with columns ['open','high','low','close']
indexed by date, most recent row last. Daily bars assumed.

Design notes
------------
Range architecture (the structure the public reverse-engineering of
fractal-style risk ranges converges on):

    center  = anchor price (blend of last close and short EMA)
    width   = k * sigma_daily * sqrt(horizon) * price     (vol-scaled)
    skew    = f(Hurst, trend direction)                   (fractal part)

  * Trending regime (H > 0.5): the range shifts WITH the trend — more room
    in the trend direction, less against it. Stronger H => stronger shift.
  * Mean-reverting regime (H < 0.5): symmetric range around the anchor.
  * Width also respects the recent Donchian channel so the range never
    detaches from actually-traded prices.

All magic numbers live in RangeParams and are meant to be FIT, not trusted:
run calibrate() against the archived MFR/hdg ranges before using live.

NOT included here (external data required):
    - implied vol / ivpd  (needs an options feed: SpotGamma Equity Hub,
      ORATS, etc. Hook provided in RangeSnapshot.iv for when you have it.)
    - volume decel/distribution tags (volume pipe, separate workstream)
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 1. Hurst exponent — rescaled range (R/S) analysis
# ---------------------------------------------------------------------------

def hurst_rs(prices: np.ndarray,
             min_window: int = 8,
             max_window: Optional[int] = None,
             n_scales: int = 10) -> float:
    """
    Hurst exponent via classical rescaled-range (R/S) analysis on log returns.

    H ~ 0.5  : random walk
    H > 0.5  : persistent / trending
    H < 0.5  : anti-persistent / mean-reverting

    Returns np.nan if the series is too short (< 2*min_window usable returns).

    Method: for a set of window sizes n, split the return series into
    non-overlapping blocks of length n; for each block compute
    R = range of cumulative demeaned sums, S = std of the block;
    average R/S across blocks; fit log(R/S) = H*log(n) + c.
    """
    prices = np.asarray(prices, dtype=float)
    prices = prices[~np.isnan(prices)]
    if len(prices) < 2 * min_window + 1:
        return float("nan")

    rets = np.diff(np.log(prices))
    n_obs = len(rets)
    max_window = max_window or n_obs // 2
    if max_window <= min_window:
        return float("nan")

    # log-spaced window sizes
    scales = np.unique(
        np.floor(np.logspace(np.log10(min_window),
                             np.log10(max_window),
                             n_scales)).astype(int))
    scales = scales[scales >= min_window]

    log_n, log_rs = [], []
    for n in scales:
        n_blocks = n_obs // n
        if n_blocks < 1:
            continue
        rs_vals = []
        for b in range(n_blocks):
            block = rets[b * n:(b + 1) * n]
            s = block.std(ddof=1)
            if s == 0 or np.isnan(s):
                continue
            dev = np.cumsum(block - block.mean())
            r = dev.max() - dev.min()
            rs_vals.append(r / s)
        if rs_vals:
            log_n.append(math.log(n))
            log_rs.append(math.log(np.mean(rs_vals)))

    if len(log_n) < 3:
        return float("nan")

    h, _c = np.polyfit(log_n, log_rs, 1)
    # clamp to sane band; estimates outside [0,1] are numerical noise
    return float(np.clip(h, 0.0, 1.0))


# ---------------------------------------------------------------------------
# 2. Realized volatility
# ---------------------------------------------------------------------------

def ewma_vol(close: pd.Series, lam: float = 0.94) -> float:
    """RiskMetrics-style EWMA daily volatility of log returns (decimal, e.g. 0.014)."""
    rets = np.log(close / close.shift(1)).dropna().to_numpy()
    if len(rets) < 10:
        return float("nan")
    var = rets[0] ** 2
    for r in rets[1:]:
        var = lam * var + (1 - lam) * r ** 2
    return float(math.sqrt(var))


def parkinson_vol(high: pd.Series, low: pd.Series, window: int = 20) -> float:
    """Parkinson high-low daily vol estimator over the trailing window (decimal)."""
    hl = np.log(high / low).tail(window).to_numpy()
    hl = hl[~np.isnan(hl)]
    if len(hl) < 5:
        return float("nan")
    return float(math.sqrt(np.mean(hl ** 2) / (4 * math.log(2))))


# ---------------------------------------------------------------------------
# 3. Range construction
# ---------------------------------------------------------------------------

@dataclass
class RangeParams:
    """Tunable parameters. Defaults are sane priors — CALIBRATE before trusting."""
    horizon_days: float = 5.0      # range is a ~1-week probable range
    k_width: float = 1.35          # width = k * sigma * sqrt(horizon)
    anchor_ema_span: int = 5       # anchor blends last close with this EMA
    anchor_close_weight: float = 0.6   # 1.0 = pure last close
    hurst_window: int = 126        # lookback for Hurst estimate (~6 months)
    hurst_skew_gain: float = 1.6   # how strongly H>0.5 shifts range with trend
    trend_fast: int = 20           # fast EMA for trend/momentum
    trend_slow: int = 60           # slow EMA for trend
    donchian_window: int = 21      # channel that tethers the range to traded prices
    donchian_blend: float = 0.35   # 0 = ignore channel, 1 = pure Donchian
    vol_blend_parkinson: float = 0.5   # blend EWMA vs Parkinson vol


@dataclass
class RangeSnapshot:
    """One name, one date: everything the SCREEN row needs (ex-options data)."""
    ticker: str
    date: str
    price: float
    low: float
    high: float
    rp: float
    hurst: float
    sigma_daily: float             # blended daily vol, decimal
    trend: str                     # BULL / BEAR / NEUT
    momentum: float                # fast-EMA slope, % per day
    iv: Optional[float] = None     # hook: fill from options feed when available
    source: str = "shadow"

    def as_row(self) -> dict:
        return asdict(self)


def _trend_state(close: pd.Series, fast: int, slow: int) -> tuple[str, float]:
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    px = close.iloc[-1]
    f, s = ema_f.iloc[-1], ema_s.iloc[-1]
    # momentum: 5-day slope of the fast EMA, in % per day
    if len(ema_f) >= 6 and ema_f.iloc[-6] > 0:
        mom = (f / ema_f.iloc[-6]) ** (1 / 5) - 1
    else:
        mom = float("nan")
    band = 0.001  # 10 bps dead zone
    if f > s * (1 + band) and px > s:
        state = "BULL"
    elif f < s * (1 - band) and px < s:
        state = "BEAR"
    else:
        state = "NEUT"
    return state, float(mom * 100 if not math.isnan(mom) else float("nan"))


def compute_range(df: pd.DataFrame,
                  ticker: str = "?",
                  params: RangeParams = None) -> RangeSnapshot:
    """
    Compute the shadow range for the LAST row of df.

    df: DataFrame with columns open, high, low, close (daily), oldest first.
        Needs >= ~60 rows for a usable answer; >= params.hurst_window
        preferred for a stable Hurst.
    """
    p = params or RangeParams()
    df = df.dropna(subset=["close"])
    if len(df) < 60:
        raise ValueError(f"{ticker}: need >=60 daily bars, got {len(df)}")

    close = df["close"]
    px = float(close.iloc[-1])

    # --- volatility (blend EWMA close-close with Parkinson OHLC) ---
    v_ewma = ewma_vol(close)
    v_park = parkinson_vol(df["high"], df["low"])
    if math.isnan(v_park):
        sigma = v_ewma
    elif math.isnan(v_ewma):
        sigma = v_park
    else:
        sigma = (1 - p.vol_blend_parkinson) * v_ewma + p.vol_blend_parkinson * v_park

    # --- Hurst on the trailing window ---
    h = hurst_rs(close.tail(p.hurst_window).to_numpy())
    h_eff = 0.5 if math.isnan(h) else h

    # --- trend & momentum ---
    trend, mom = _trend_state(close, p.trend_fast, p.trend_slow)

    # --- anchor ---
    ema_a = close.ewm(span=p.anchor_ema_span, adjust=False).mean().iloc[-1]
    anchor = p.anchor_close_weight * px + (1 - p.anchor_close_weight) * float(ema_a)

    # --- base width, vol-scaled ---
    half_width = p.k_width * sigma * math.sqrt(p.horizon_days) * anchor

    # --- fractal skew: trending regime shifts the range with the trend ---
    # skew in [-1, 1] * hurst_skew_gain * (H - 0.5) * direction
    direction = {"BULL": 1.0, "BEAR": -1.0, "NEUT": 0.0}[trend]
    skew = p.hurst_skew_gain * (h_eff - 0.5) * direction   # e.g. H=0.65 BULL -> +0.24
    skew = float(np.clip(skew, -0.6, 0.6))
    lo = anchor - half_width * (1 - skew)
    hi = anchor + half_width * (1 + skew)

    # --- tether to the Donchian channel of actually-traded prices ---
    d_hi = float(df["high"].tail(p.donchian_window).max())
    d_lo = float(df["low"].tail(p.donchian_window).min())
    b = p.donchian_blend
    lo = (1 - b) * lo + b * d_lo
    hi = (1 - b) * hi + b * d_hi
    if hi <= lo:  # degenerate guard
        hi = lo * 1.001

    rp = (px - lo) / (hi - lo)

    return RangeSnapshot(
        ticker=ticker,
        date=str(df.index[-1].date()) if hasattr(df.index[-1], "date") else str(df.index[-1]),
        price=round(px, 4),
        low=round(lo, 4),
        high=round(hi, 4),
        rp=round(float(rp), 3),
        hurst=round(h, 3) if not math.isnan(h) else float("nan"),
        sigma_daily=round(sigma, 5) if not math.isnan(sigma) else float("nan"),
        trend=trend,
        momentum=round(mom, 3) if not math.isnan(mom) else float("nan"),
    )


def compute_range_history(df: pd.DataFrame,
                          ticker: str = "?",
                          params: RangeParams = None,
                          start: int = 130) -> pd.DataFrame:
    """Walk-forward: compute the snapshot for every day from `start` onward.
    Used by the calibration harness and for coverage stats."""
    rows = []
    for i in range(start, len(df) + 1):
        try:
            snap = compute_range(df.iloc[:i], ticker=ticker, params=params)
            rows.append(snap.as_row())
        except ValueError:
            continue
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.set_index("date")
    return out


# ---------------------------------------------------------------------------
# 4. Calibration harness — fit params against archived MFR / hdg ranges
# ---------------------------------------------------------------------------

@dataclass
class CalibResult:
    params: RangeParams
    mae_low_pct: float      # mean abs error of low edge, % of price
    mae_high_pct: float     # mean abs error of high edge, % of price
    rp_mae: float           # mean abs error of rp vs reference rp
    coverage: float         # % of reference closes inside the shadow range
    n_obs: int

    def score(self) -> float:
        """Single fitness number (lower = better)."""
        return self.mae_low_pct + self.mae_high_pct + 2.0 * self.rp_mae


def evaluate_params(price_data: dict[str, pd.DataFrame],
                    reference: pd.DataFrame,
                    params: RangeParams) -> CalibResult:
    """
    price_data: {ticker: OHLC DataFrame}
    reference : DataFrame with columns [ticker, date, ref_low, ref_high]
                — the archived MFR (or hdg) ranges, KNOWN-GOOD rows only.
                date as string YYYY-MM-DD matching the price index dates.
    """
    errs_lo, errs_hi, errs_rp, inside = [], [], [], []
    for tkr, grp in reference.groupby("ticker"):
        df = price_data.get(tkr)
        if df is None:
            continue
        hist = compute_range_history(df, ticker=tkr, params=params)
        if hist.empty:
            continue
        merged = grp.assign(date=grp["date"].astype(str)).merge(
            hist.reset_index(), on="date", how="inner", suffixes=("", "_shadow"))
        for _, r in merged.iterrows():
            px = r["price"]
            if px <= 0 or r["ref_high"] <= r["ref_low"]:
                continue
            errs_lo.append(abs(r["low"] - r["ref_low"]) / px * 100)
            errs_hi.append(abs(r["high"] - r["ref_high"]) / px * 100)
            ref_rp = (px - r["ref_low"]) / (r["ref_high"] - r["ref_low"])
            errs_rp.append(abs(r["rp"] - ref_rp))
            inside.append(r["ref_low"] <= px <= r["ref_high"])
    n = len(errs_lo)
    if n == 0:
        return CalibResult(params, float("inf"), float("inf"), float("inf"), 0.0, 0)
    return CalibResult(
        params=params,
        mae_low_pct=float(np.mean(errs_lo)),
        mae_high_pct=float(np.mean(errs_hi)),
        rp_mae=float(np.mean(errs_rp)),
        coverage=float(np.mean(inside)),
        n_obs=n,
    )


def calibrate(price_data: dict[str, pd.DataFrame],
              reference: pd.DataFrame,
              grid: Optional[dict] = None,
              verbose: bool = True) -> CalibResult:
    """
    Grid-search the key parameters against the archive. Keep the grid modest —
    this recomputes walk-forward histories per combo. Refine in two passes
    (coarse grid, then a tight grid around the winner) rather than one huge one.
    """
    grid = grid or {
        "k_width": [1.0, 1.35, 1.7],
        "horizon_days": [3.0, 5.0],
        "hurst_skew_gain": [0.8, 1.6, 2.4],
        "donchian_blend": [0.2, 0.35, 0.5],
    }
    keys = list(grid)
    best: Optional[CalibResult] = None
    for combo in itertools.product(*grid.values()):
        p = RangeParams(**dict(zip(keys, combo)))
        res = evaluate_params(price_data, reference, p)
        if verbose:
            print(f"  {dict(zip(keys, combo))} -> "
                  f"lo±{res.mae_low_pct:.2f}% hi±{res.mae_high_pct:.2f}% "
                  f"rpMAE {res.rp_mae:.3f} cov {res.coverage:.0%} (n={res.n_obs})")
        if best is None or res.score() < best.score():
            best = res
    return best


# ---------------------------------------------------------------------------
# 5. Live-feed validator — flag MFR values diverging from the shadow
# ---------------------------------------------------------------------------

def validate_feed(shadow: RangeSnapshot,
                  feed_low: float, feed_high: float,
                  rp_tol: float = 0.25,
                  width_ratio_tol: float = 2.0) -> list[str]:
    """
    Compare a live MFR range against the shadow calculation.
    Returns a list of human-readable flags (empty = looks sane).
    """
    flags = []
    px = shadow.price
    if feed_high <= feed_low:
        return [f"{shadow.ticker}: feed range inverted ({feed_low}-{feed_high})"]
    feed_rp = (px - feed_low) / (feed_high - feed_low)
    if abs(feed_rp - shadow.rp) > rp_tol:
        flags.append(f"{shadow.ticker}: rp divergence feed={feed_rp:.2f} "
                     f"shadow={shadow.rp:.2f}")
    feed_w = (feed_high - feed_low) / px
    shad_w = (shadow.high - shadow.low) / px
    if shad_w > 0 and not (1 / width_ratio_tol <= feed_w / shad_w <= width_ratio_tol):
        flags.append(f"{shadow.ticker}: width divergence feed={feed_w:.1%} "
                     f"shadow={shad_w:.1%} of price")
    if not (feed_low * 0.5 <= px <= feed_high * 1.5):
        flags.append(f"{shadow.ticker}: price far outside feed range — stale feed?")
    return flags


# ---------------------------------------------------------------------------
# 6. Self-test on synthetic data (runs offline, no network needed)
# ---------------------------------------------------------------------------

def _synthetic_ohlc(n: int, mu: float, sigma: float, seed: int,
                    mean_revert: float = 0.0,
                    persist: float = 0.0) -> pd.DataFrame:
    """GBM daily OHLC generator for testing. mean_revert>0 gives an OU flavor
    (anti-persistent); persist>0 gives AR(1)-autocorrelated returns (trending
    in the Hurst sense — note plain drift does NOT raise H, since R/S demeans)."""
    rng = np.random.default_rng(seed)
    log_p = [math.log(100.0)]
    prev_shock = 0.0
    for _ in range(n - 1):
        drift = mu - mean_revert * (log_p[-1] - math.log(100.0))
        shock = persist * prev_shock + sigma * rng.standard_normal()
        prev_shock = shock
        log_p.append(log_p[-1] + drift + shock)
    close = np.exp(log_p)
    intraday = np.abs(rng.normal(0, sigma, n)) * close
    high = close + intraday * rng.uniform(0.3, 1.0, n)
    low = close - intraday * rng.uniform(0.3, 1.0, n)
    openp = np.clip(close + rng.normal(0, sigma / 2, n) * close, low, high)
    idx = pd.bdate_range("2025-06-02", periods=n)
    return pd.DataFrame({"open": openp, "high": high, "low": low, "close": close},
                        index=idx)


if __name__ == "__main__":
    print("=== shadow_range self-test (synthetic data) ===\n")

    # 1) Hurst sanity: trending series should read > random walk > mean-reverting
    trend_df = _synthetic_ohlc(300, mu=0.0008, sigma=0.008, seed=1, persist=0.35)
    rw_df    = _synthetic_ohlc(300, mu=0.0,    sigma=0.012, seed=2)
    mr_df    = _synthetic_ohlc(300, mu=0.0,    sigma=0.012, seed=3, mean_revert=0.15)
    h_tr = hurst_rs(trend_df["close"].to_numpy())
    h_rw = hurst_rs(rw_df["close"].to_numpy())
    h_mr = hurst_rs(mr_df["close"].to_numpy())
    print(f"Hurst  trending={h_tr:.3f}  random_walk={h_rw:.3f}  mean_revert={h_mr:.3f}")
    assert h_tr > h_mr, "Hurst ordering failed: trending should exceed mean-reverting"

    # 2) Range snapshots
    for name, df in [("TREND", trend_df), ("RW", rw_df), ("MR", mr_df)]:
        s = compute_range(df, ticker=name)
        print(f"{name:6s} px={s.price:8.2f} rng[{s.low:8.2f}-{s.high:8.2f}] "
              f"rp={s.rp:5.2f} H={s.hurst:.2f} vol={s.sigma_daily:.4f} "
              f"trend={s.trend} mom={s.momentum:+.2f}%/d")
        assert s.high > s.low

    # 3) Walk-forward coverage: % of next-day closes inside today's range
    hist = compute_range_history(rw_df, ticker="RW", start=130)
    nxt = rw_df["close"].shift(-1)
    nxt.index = [str(d.date()) for d in nxt.index]
    j = hist.join(nxt.rename("next_close"), how="inner").dropna(subset=["next_close"])
    cov = ((j["next_close"] >= j["low"]) & (j["next_close"] <= j["high"])).mean()
    print(f"\nWalk-forward next-day coverage (RW): {cov:.0%} over {len(j)} days "
          f"(target: high 80s–low 90s for a ~1-week range)")

    # 4) Validator demo: feed a corrupted range, expect flags
    s = compute_range(rw_df, ticker="RW")
    bad_flags = validate_feed(s, feed_low=s.low * 1.6, feed_high=s.high * 1.7)
    good_flags = validate_feed(s, feed_low=s.low * 0.995, feed_high=s.high * 1.005)
    print(f"\nValidator: corrupted feed -> {len(bad_flags)} flag(s): {bad_flags}")
    print(f"Validator: healthy feed   -> {len(good_flags)} flag(s)")
    assert bad_flags and not good_flags

    # 5) Tiny calibration demo: pretend the 'reference' ranges are the shadow's
    #    own output with k_width=1.7, and confirm the harness prefers 1.7.
    ref_params = RangeParams(k_width=1.7)
    ref_hist = compute_range_history(rw_df, ticker="RW", params=ref_params, start=200)
    reference = (ref_hist.reset_index()
                 .rename(columns={"low": "ref_low", "high": "ref_high"})
                 [["ticker", "date", "ref_low", "ref_high"]])
    print("\nCalibration demo (truth k_width=1.7):")
    best = calibrate({"RW": rw_df}, reference,
                     grid={"k_width": [1.0, 1.35, 1.7]}, verbose=True)
    print(f"  -> winner k_width={best.params.k_width} (expected 1.7)")
    assert best.params.k_width == 1.7

    print("\nAll self-tests passed.")
