"""shadow_ingest.py — compute shadow ranges for the universe, in parallel with MFR.

Pulls daily OHLC from yfinance for every name in v_screener, runs
shadow_range.compute_range() over it, and upserts the RangeSnapshot into
shadow_snapshots (migration 074).

This is a PARALLEL computation, never a replacement:
  * It writes only to shadow_snapshots. mfr_snapshots is never touched.
  * Nothing reads shadow_snapshots yet — no report output changes.

Names with fewer than 60 daily bars store NULLs with status='insufficient_bars'
rather than crashing the run (compute_range raises ValueError below 60).

IMPORTANT: RangeParams defaults are uncalibrated priors. Per
SHADOW_RANGE_INTEGRATION.md step 2, these numbers must be calibrated against
known-good archived MFR/hdg ranges before anything downstream trusts them.

CLI:
    python -m tools.shadow_ingest              # full universe
    python -m tools.shadow_ingest --limit 25   # smoke-test a subset
    python -m tools.shadow_ingest --dry-run    # compute, print, do not write
    python -m tools.shadow_ingest --compare 10 # write, then show MFR vs shadow
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict
from datetime import date

log = logging.getLogger(__name__)

# yfinance history pulled per name. compute_range needs >=60 bars; Hurst wants
# ~126 (RangeParams.hurst_window) to stabilise, and the integration notes ask
# for >=130. 1y of daily bars is ~252 sessions, comfortably above that.
_PERIOD = "1y"
_CHUNK = 120
_MIN_BARS = 60


def _params_hash(params) -> str:
    """Short fingerprint of the RangeParams actually used, so a stored row can be
    traced back to the parameter set that produced it (they are uncalibrated)."""
    import hashlib
    blob = "|".join(f"{k}={v}" for k, v in sorted(asdict(params).items()))
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def fetch_universe(limit: int | None = None) -> list[str]:
    """Every ticker in v_screener — including names with no live MFR range, since
    those (the dark names) are exactly where a shadow range is most useful."""
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT ticker FROM v_screener WHERE ticker IS NOT NULL "
                    "ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]
    return tickers[:limit] if limit else tickers


def fetch_ohlc_batch(tickers, period: str = _PERIOD, chunk: int = _CHUNK) -> dict:
    """{bot_ticker: DataFrame[open, high, low, close]} — oldest first.

    Batched yf.download (per-ticker fetch rate-limits past ~300 names). Reuses
    weekend_report._yf_symbol so futures/crypto/FX map the same way everywhere.
    """
    import pandas as pd
    try:
        import yfinance as yf
    except Exception as e:
        log.warning("shadow: yfinance unavailable: %s", e)
        return {}
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE
    except Exception:
        HEDGEYE_TO_YFINANCE = {}
    from tools.weekend_report import _yf_symbol

    fwd = {t: _yf_symbol(t, HEDGEYE_TO_YFINANCE) for t in tickers}
    ysyms = list(dict.fromkeys(v for v in fwd.values() if v))

    by_ysym: dict = {}
    for i in range(0, len(ysyms), chunk):
        part = ysyms[i:i + chunk]
        try:
            df = yf.download(part, period=period, interval="1d", group_by="ticker",
                             auto_adjust=True, progress=False, threads=True)
        except Exception as e:
            log.warning("shadow: yf.download chunk failed: %s", e)
            continue
        for s in part:
            try:
                sub = df[s] if len(part) > 1 else df
                if sub is None or sub.empty:
                    continue
                out = pd.DataFrame({
                    "open": sub["Open"], "high": sub["High"],
                    "low": sub["Low"], "close": sub["Close"],
                }).dropna(subset=["close"])
                if not out.empty:
                    by_ysym[s] = out
            except Exception:
                continue
    return {t: by_ysym.get(y) for t, y in fwd.items()}


def compute_for_universe(tickers, bars_by_ticker, params=None) -> list[dict]:
    """Run compute_range per name. Never raises — every ticker yields one row,
    with status recording why the numbers are NULL when they are."""
    from shadow_range import compute_range, RangeParams
    p = params or RangeParams()
    phash = _params_hash(p)
    today = date.today()
    rows = []
    for t in tickers:
        df = bars_by_ticker.get(t)
        base = {"ticker": t, "snapshot_date": today, "params_hash": phash,
                "shadow_price": None, "shadow_low": None, "shadow_high": None,
                "shadow_rp": None, "shadow_hurst": None, "shadow_sigma": None,
                "shadow_trend": None, "shadow_momentum": None, "note": None}
        if df is None or len(df) == 0:
            rows.append({**base, "bars": 0, "status": "no_data",
                         "note": "no yfinance bars returned"})
            continue
        n = len(df)
        if n < _MIN_BARS:
            # Requirement: store nulls, do not crash.
            rows.append({**base, "bars": n, "status": "insufficient_bars",
                         "note": f"{n} bars < {_MIN_BARS}"})
            continue
        try:
            s = compute_range(df, ticker=t, params=p)
        except Exception as e:                       # never kill the run
            rows.append({**base, "bars": n, "status": "error", "note": str(e)[:200]})
            continue

        def _f(x):
            """NaN -> None so Postgres stores NULL, not NaN."""
            return None if x is None or x != x else float(x)

        rows.append({**base, "bars": n, "status": "ok",
                     "shadow_price": _f(s.price), "shadow_low": _f(s.low),
                     "shadow_high": _f(s.high), "shadow_rp": _f(s.rp),
                     "shadow_hurst": _f(s.hurst), "shadow_sigma": _f(s.sigma_daily),
                     "shadow_trend": s.trend, "shadow_momentum": _f(s.momentum)})
    return rows


def persist(rows) -> int:
    """Upsert into shadow_snapshots. Idempotent on (ticker, snapshot_date)."""
    import db_pg
    if not rows:
        return 0
    cols = ("ticker", "snapshot_date", "shadow_price", "shadow_low", "shadow_high",
            "shadow_rp", "shadow_hurst", "shadow_sigma", "shadow_trend",
            "shadow_momentum", "bars", "status", "note", "params_hash")
    sql = (
        f"INSERT INTO shadow_snapshots ({', '.join(cols)}) "
        f"VALUES ({', '.join(['%s'] * len(cols))}) "
        "ON CONFLICT (ticker, snapshot_date) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[2:])
        + ", computed_at = now()"
    )
    n = 0
    with db_pg.get_conn() as c, c.cursor() as cur:
        for r in rows:
            cur.execute(sql, tuple(r[c] for c in cols))
            n += 1
        c.commit()
    return n


def run(limit: int | None = None, dry_run: bool = False) -> dict:
    tickers = fetch_universe(limit)
    log.info("shadow: universe = %d tickers", len(tickers))
    bars = fetch_ohlc_batch(tickers)
    rows = compute_for_universe(tickers, bars)
    counts: dict = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    written = 0 if dry_run else persist(rows)
    return {"universe": len(tickers), "rows": len(rows), "written": written,
            "counts": counts, "dry_run": dry_run}


def compare_sample(n: int = 10) -> str:
    """MFR range/rp vs shadow range/rp, side by side, for spot-checking."""
    import db_pg
    sql = """
        WITH latest_mfr AS (
          SELECT DISTINCT ON (ticker) ticker, snapshot_date, price,
                 range_low, range_high, hurst
          FROM mfr_snapshots ORDER BY ticker, snapshot_date DESC)
        SELECT s.ticker, m.snapshot_date, m.price,
               m.range_low, m.range_high,
               (m.price - m.range_low) / NULLIF(m.range_high - m.range_low, 0) AS mfr_rp,
               m.hurst,
               s.shadow_low, s.shadow_high, s.shadow_rp, s.shadow_hurst, s.bars
        FROM shadow_snapshots s
        JOIN latest_mfr m ON m.ticker = s.ticker
        WHERE s.snapshot_date = (SELECT max(snapshot_date) FROM shadow_snapshots)
          AND s.status = 'ok' AND m.range_low IS NOT NULL
        ORDER BY s.ticker
        LIMIT %s
    """
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, (n,))
        rows = cur.fetchall()
    L = [f"{'ticker':8} {'mfr_date':11} {'price':>9} "
         f"{'MFR low':>9} {'MFR high':>9} {'MFRrp':>6} "
         f"{'SHD low':>9} {'SHD high':>9} {'SHDrp':>6} "
         f"{'MFR H':>6} {'SHD H':>6} {'bars':>5}",
         "-" * 118]
    for (tk, sd, px, ml, mh, mrp, mh2, sl, sh, srp, sh2, bars) in rows:
        def f(x, w=9, p=2):
            return f"{float(x):>{w}.{p}f}" if x is not None else " " * (w - 1) + "-"
        L.append(f"{tk:8} {str(sd):11} {f(px)} {f(ml)} {f(mh)} {f(mrp, 6, 2)} "
                 f"{f(sl)} {f(sh)} {f(srp, 6, 2)} {f(mh2, 6, 2)} {f(sh2, 6, 2)} "
                 f"{bars:>5}")
    return "\n".join(L)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--compare", type=int, default=0,
                    help="after the run, print N names MFR vs shadow")
    a = ap.parse_args()
    summary = run(limit=a.limit, dry_run=a.dry_run)
    print(f"\nshadow ingest: {summary}")
    if a.compare:
        print("\n" + compare_sample(a.compare))
