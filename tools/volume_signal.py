"""Volume signal — vol_vs_20d + vol_slope_3d (Keith's "dips on decelerating
volume" rule).

For each ticker it computes, from the daily OHLCV feed:
  * vol_vs_20d   — latest volume / trailing-20-session average. >1 = heavy
    participation, <1 = quiet.
  * vol_slope_3d — normalized slope of volume across the last 3 DOWN days
    (close < prior close). < 0 = volume decelerating into weakness (seller
    exhaustion → a real, buyable dip). > 0 while price is falling =
    distribution (same range position, opposite trade).
  * decelerating = vol_slope_3d < 0
  * price_down_3d = net 3-session return < 0
  * real_dip = price_down_3d AND decelerating  ← the buyable case

This is the trigger the framework names in every alert but the bot never had
the data for. Pairs with the RS grid: PASS_THE_PUCK + real_dip = high
conviction; PASS_THE_PUCK + distribution = wait.

Price/volume come through fetch_ohlcv() — yfinance by default (volume rides
along with the close), same vendor seam as tools.relative_strength; a paid
daily-OHLCV feed drops in at the marked hook. mfr_snapshots.previous_day_volume
is the ongoing cross-check.

CLI:
    python -m tools.volume_signal            # compute + persist
    python -m tools.volume_signal --dry-run  # compute + print only
"""

from __future__ import annotations

import os
import sys
import logging
import datetime as dt
from typing import Optional

log = logging.getLogger(__name__)

# Universe: reuse the RS universe (sectors + bond/credit/dollar ETFs). Falls
# back to a local copy so the module is importable/testable standalone.
try:  # pragma: no cover - import path depends on runtime
    from tools.relative_strength import UNIVERSE as _RS_UNIVERSE
    UNIVERSE = list(_RS_UNIVERSE)
except Exception:
    UNIVERSE = ["XLK", "XLV", "XLF", "XLE", "XLI", "XLY", "XLP", "XLU",
                "XLB", "XLRE", "XLC", "TLT", "SHY", "HYG", "LQD", "UUP"]

AVG_WINDOW = 20        # sessions for the volume average
DOWN_DAYS = 3          # number of trailing down days for the slope
DOWN_LOOKBACK = 12     # search this many recent sessions to find down days
PRICE_WINDOW = 3       # sessions for the net price-change context


# ── Price/volume vendor seam ─────────────────────────────────────────────────
def fetch_ohlcv(ticker: str, lookback_days: int) -> list[tuple[str, float, float]]:
    """Return [(iso_date, close, volume)] oldest->newest for the last
    `lookback_days` sessions. Default vendor yfinance; a paid daily-OHLCV feed
    drops in at the hook with the same return shape."""
    vendor = os.environ.get("VOL_PRICE_VENDOR", "yfinance").lower()
    if vendor == "yfinance":
        return _yf_ohlcv(ticker, lookback_days)
    # ── paid-vendor hook (Polygon / Tiingo / EODHD) ──────────────────────────
    raise NotImplementedError(
        f"VOL_PRICE_VENDOR={vendor!r} not wired; only 'yfinance' available."
    )


def _yf_ohlcv(ticker: str, lookback_days: int) -> list[tuple[str, float, float]]:
    try:
        import yfinance as yf
    except Exception as e:  # pragma: no cover
        log.warning("yfinance unavailable: %s", e)
        return []
    try:
        period = f"{int(lookback_days * 1.6) + 15}d"
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df is None or df.empty or "Close" not in df or "Volume" not in df:
            return []
        out: list[tuple[str, float, float]] = []
        for idx, row in df.iterrows():
            c, v = row["Close"], row["Volume"]
            if c != c or v != v:  # NaN
                continue
            out.append((idx.date().isoformat(), float(c), float(v)))
        return out[-lookback_days:] if lookback_days else out
    except Exception as e:  # pragma: no cover
        log.warning("yfinance ohlcv failed for %s: %s", ticker, e)
        return []


def _all_assets() -> list[str]:
    """EVERY asset the bot tracks — the union of all mfr_snapshots tickers and
    the full ticker_tags roster (not just names with a defined range). Names
    with no yfinance volume (indices, spot symbols, thin tickers) simply skip
    downstream. Falls back to the 16-ETF UNIVERSE if the DB is unreachable."""
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ticker FROM mfr_snapshots "
                "UNION SELECT ticker FROM ticker_tags")
            names = sorted({r[0] for r in cur.fetchall() if r[0]})
        return names or list(UNIVERSE)
    except Exception as e:
        log.warning("volume: full asset universe unavailable (%s) - using 16 ETFs", e)
        return list(UNIVERSE)


def fetch_ohlcv_batch(tickers, lookback_days):
    """{ticker: [(iso_date, close, volume)]} via ONE batched yf.download for the
    whole set (per-ticker fetch rate-limits past ~300 names). yfinance only;
    a paid vendor falls back to per-ticker fetch_ohlcv."""
    vendor = os.environ.get("VOL_PRICE_VENDOR", "yfinance").lower()
    if vendor != "yfinance":
        return {t: fetch_ohlcv(t, lookback_days) for t in tickers}
    try:
        import yfinance as yf
    except Exception as e:
        log.warning("yfinance unavailable: %s", e)
        return {}
    uniq = sorted({t for t in tickers if t})
    if not uniq:
        return {}
    period = f"{int(lookback_days * 1.6) + 15}d"
    try:
        df = yf.download(uniq, period=period, interval="1d", group_by="ticker",
                         threads=True, progress=False, auto_adjust=False)
    except Exception as e:
        log.warning("volume: batch download failed (%s) - per-ticker fallback", e)
        return {t: fetch_ohlcv(t, lookback_days) for t in uniq}
    out = {}
    for t in uniq:
        try:
            sub = df[t] if len(uniq) > 1 else df
            if sub is None or sub.empty or "Close" not in sub or "Volume" not in sub:
                continue
            bars = []
            for idx, row in sub.iterrows():
                c, v = row["Close"], row["Volume"]
                if c != c or v != v:
                    continue
                bars.append((idx.date().isoformat(), float(c), float(v)))
            if bars:
                out[t] = bars[-lookback_days:] if lookback_days else bars
        except Exception:
            continue
    return out


# ── Pure math (no I/O — unit-testable) ───────────────────────────────────────
def vol_vs_20d(volumes: list[float], window: int = AVG_WINDOW) -> tuple[Optional[float], Optional[float]]:
    """(vol_vs_20d, avg) — latest volume over the average of the `window`
    sessions before it. None if not enough history."""
    if len(volumes) < window + 1:
        return None, None
    prior = volumes[-1 - window:-1]
    avg = sum(prior) / len(prior)
    if avg <= 0:
        return None, avg
    return volumes[-1] / avg, avg


def _norm_slope(ys: list[float]) -> Optional[float]:
    """Normalized per-step OLS slope (slope / mean). Sign is the tell."""
    n = len(ys)
    if n < 2:
        return None
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0 or my == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return slope / my


def down_day_volume_slope(closes: list[float], volumes: list[float],
                          n_down: int = DOWN_DAYS,
                          lookback: int = DOWN_LOOKBACK) -> tuple[Optional[float], int]:
    """Normalized slope of volume across the last `n_down` DOWN days (close <
    prior close) found within the trailing `lookback` sessions. Returns
    (slope, n_used). Negative slope = volume decelerating across successive
    down days."""
    if len(closes) < 2:
        return None, 0
    m = min(lookback, len(closes) - 1)
    down_idx = []
    # walk backwards over recent sessions collecting down-day indices
    for i in range(len(closes) - 1, len(closes) - 1 - m, -1):
        if closes[i] < closes[i - 1]:
            down_idx.append(i)
        if len(down_idx) >= n_down:
            break
    if len(down_idx) < 2:
        return None, len(down_idx)
    down_idx = sorted(down_idx)                # chronological
    vols = [volumes[i] for i in down_idx]
    return _norm_slope(vols), len(down_idx)


def decel_streak(closes: list[float], volumes: list[float]) -> int:
    """Consecutive sessions ending today that are 'decelerating' — a down day
    (close < prior close) on lighter volume (volume < prior volume). Counts
    back from the latest session and stops at the first day that doesn't
    qualify. 0 = today isn't a decelerating down day. "3rd day of decel vol" = 3.
    """
    n = min(len(closes), len(volumes))
    streak = 0
    for i in range(n - 1, 0, -1):
        if closes[i] < closes[i - 1] and volumes[i] < volumes[i - 1]:
            streak += 1
        else:
            break
    return streak


def price_change(closes: list[float], window: int = PRICE_WINDOW) -> Optional[float]:
    if len(closes) <= window:
        return None
    past = closes[-1 - window]
    if not past:
        return None
    return closes[-1] / past - 1.0


# ── Orchestration ────────────────────────────────────────────────────────────
def _compute(universe=None) -> dict:
    lookback = AVG_WINDOW + DOWN_LOOKBACK + 15
    names = universe if universe is not None else _all_assets()
    batch = fetch_ohlcv_batch(names, lookback)
    rows: dict[str, dict] = {}
    for t in names:
        bars = batch.get(t) or []
        if len(bars) < AVG_WINDOW + 2:
            log.warning("insufficient volume history for %s (n=%d)", t, len(bars))
            continue
        closes = [b[1] for b in bars]
        volumes = [b[2] for b in bars]
        vv, avg = vol_vs_20d(volumes)
        slope, n_used = down_day_volume_slope(closes, volumes)
        pchg = price_change(closes)
        streak = decel_streak(closes, volumes)
        decel = slope is not None and slope < 0
        pdown = pchg is not None and pchg < 0
        rows[t] = {
            "ticker": t,
            "volume": int(volumes[-1]),
            "avg_vol_20d": int(avg) if avg else None,
            "vol_vs_20d": round(vv, 4) if vv is not None else None,
            "vol_slope_3d": round(slope, 8) if slope is not None else None,
            "down_days_used": n_used,
            "decel_streak": streak,
            "decelerating": decel,
            "price_down_3d": pdown,
            "real_dip": bool(pdown and decel),
            "n_obs": len(volumes),
        }
    return {"rows": rows}


def _print(payload: dict) -> None:
    rows = sorted(payload["rows"].values(), key=lambda r: r["ticker"])
    print("\nVOLUME SIGNAL  (real_dip = price down + volume decelerating)\n")
    hdr = f"{'TKR':<5} {'vs20d':>7} {'slope3d':>10} {'dnDays':>6} {'strk':>4} {'px<0':>5} {'DECEL':>6} {'REAL_DIP':>9}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        vv = f"{r['vol_vs_20d']:.2f}" if r["vol_vs_20d"] is not None else "n/a"
        sl = f"{r['vol_slope_3d']:+.5f}" if r["vol_slope_3d"] is not None else "n/a"
        print(f"{r['ticker']:<5} {vv:>7} {sl:>10} {r['down_days_used']:>6} "
              f"{r['decel_streak']:>4} "
              f"{('yes' if r['price_down_3d'] else 'no'):>5} "
              f"{('yes' if r['decelerating'] else 'no'):>6} "
              f"{('YES' if r['real_dip'] else '-'):>9}")
    dips = [f"{r['ticker']}({r['decel_streak']}d)" for r in rows if r["real_dip"]]
    dist = [r["ticker"] for r in rows
            if r["price_down_3d"] and not r["decelerating"]]
    print(f"\nreal dips (buyable weakness): {', '.join(dips) or 'none'}")
    print(f"distribution (down on rising vol — avoid): {', '.join(dist) or 'none'}")
    print()


def render_report_block(max_names: int = 6) -> str:
    """Compact VOLUME line for REPORT / REPORT NOW / DAYPACK. READ-ONLY —
    reads the latest volume_snapshots; fetches nothing; never raises."""
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, real_dip, price_down_3d, decelerating, vol_vs_20d,
                       COALESCE(decel_streak, 0)
                FROM volume_snapshots
                WHERE snapshot_date = (SELECT max(snapshot_date) FROM volume_snapshots)
                ORDER BY ticker
                """
            )
            rows = cur.fetchall()
        if not rows:
            return "VOLUME: no snapshot (run tools.volume_signal)"
        dip_rows = [(t, sk) for (t, rd, pd, de, vv, sk) in rows if rd]
        dip_rows.sort(key=lambda x: x[1], reverse=True)   # longest streak first
        dips = [f"{t}({sk}d)" for t, sk in dip_rows]
        dist = [t for (t, rd, pd, de, vv, sk) in rows if pd and not de]
        return (f"VOLUME (decel dip = buyable · rising vol on down = avoid): "
                f"real dips {' '.join(dips[:max_names]) or 'none'} · "
                f"distribution {' '.join(dist[:max_names]) or 'none'}")
    except Exception as e:
        log.warning("volume render failed: %s", e)
        return f"VOLUME: unavailable ({e})"


def _persist(payload: dict, snapshot_date: dt.date) -> int:
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr)
        return 2
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for r in payload["rows"].values():
                try:
                    cur.execute(
                        """
                        INSERT INTO volume_snapshots
                          (snapshot_date, ticker, volume, avg_vol_20d, vol_vs_20d,
                           vol_slope_3d, down_days_used, decel_streak, decelerating,
                           price_down_3d, real_dip, n_obs, source)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'yfinance')
                        ON CONFLICT (snapshot_date, ticker) DO UPDATE SET
                           volume=EXCLUDED.volume, avg_vol_20d=EXCLUDED.avg_vol_20d,
                           vol_vs_20d=EXCLUDED.vol_vs_20d, vol_slope_3d=EXCLUDED.vol_slope_3d,
                           down_days_used=EXCLUDED.down_days_used,
                           decel_streak=EXCLUDED.decel_streak,
                           decelerating=EXCLUDED.decelerating,
                           price_down_3d=EXCLUDED.price_down_3d, real_dip=EXCLUDED.real_dip,
                           n_obs=EXCLUDED.n_obs, computed_at=NOW()
                        """,
                        (snapshot_date, r["ticker"], r["volume"], r["avg_vol_20d"],
                         r["vol_vs_20d"], r["vol_slope_3d"], r["down_days_used"],
                         r["decel_streak"], r["decelerating"], r["price_down_3d"],
                         r["real_dip"], r["n_obs"]),
                    )
                except Exception as e:
                    log.warning("volume insert failed for %s: %s", r["ticker"], e)
            conn.commit()
        return 0
    except Exception as e:
        print(f"ERROR: persistence failed: {e}", file=sys.stderr)
        return 3


def run(dry_run: bool = False) -> int:
    today = dt.date.today()
    payload = _compute()
    if not payload["rows"]:
        print("ERROR: no volume rows computed (feed returned nothing).",
              file=sys.stderr)
        return 4
    _print(payload)
    if dry_run:
        print("[dry-run] no persistence.")
        return 0
    return _persist(payload, today)


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.volume_signal")
    p.add_argument("--dry-run", action="store_true",
                   help="compute + print only; no persistence")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(_cli())
