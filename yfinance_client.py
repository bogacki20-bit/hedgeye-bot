"""Yahoo Finance per-ticker daily snapshot client.

Third leg of the lockstep refresh trio with mfr_client + spotgamma_client.
Same shape: fetch_raw / fetch_and_save / refresh_for_tickers / latest /
is_stale. When a Hedgeye email surfaces a ticker, this module captures
that day's OHLCV + prev-close into yahoo_snapshots so the bot has a
point-in-time price record aligned with the MFR + SpotGamma fetches.

As of 2026-05-24 the underlying fetch routes through the PRICE_FEED env
flag — 'yfinance' (default) or 'polygon' (uses tools.price_feed_polygon).
The yahoo_snapshots table name stays the same regardless of feed for
schema stability.

Mapping: relies on price_monitor.HEDGEYE_TO_YFINANCE for Hedgeye-label →
yfinance-symbol translation (e.g. SPX → ^GSPC, BITCOIN → BTC-USD). If a
ticker isn't in that map, we try the ticker itself as the Yahoo symbol
and log a debug message if it 404s.

CLI smoke test:
    py yfinance_client.py --refresh --tickers OIH XOP
    py yfinance_client.py --latest OIH
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date as date_cls, datetime
from typing import Optional

import psycopg2.extras

from db_pg import get_conn

log = logging.getLogger(__name__)


# ─────────────────────────── Symbol mapping ───────────────────────────

def _resolve_yf_symbol(ticker: str) -> Optional[str]:
    """Translate a Hedgeye ticker label to its yfinance symbol via the
    canonical map in price_monitor.HEDGEYE_TO_YFINANCE. Falls back to the
    ticker itself if not in the map.
    """
    try:
        # Lazy import — price_monitor pulls heavy deps and we want this module
        # to remain importable even if price_monitor's deps aren't all there.
        from price_monitor import HEDGEYE_TO_YFINANCE
    except Exception:
        HEDGEYE_TO_YFINANCE = {}
    sym = HEDGEYE_TO_YFINANCE.get(ticker.upper())
    if sym:
        return sym
    # Fall back to the ticker itself (covers single-stocks not in the map).
    return ticker.upper()


# ─────────────────────────── Fetch ───────────────────────────

def fetch_raw(ticker: str) -> Optional[dict]:
    """Fetch today's OHLCV + prev close for `ticker`.

    Routes through PRICE_FEED env flag — 'yfinance' (default) or 'polygon'.
    Returns a flat dict with: yf_symbol, snapshot_date, price, open, high,
    low, close, prev_close, daily_change, daily_change_pct, volume,
    full_payload.

    Returns None on 404, malformed data, or if the feed is unavailable.
    Never raises.
    """
    feed = (os.environ.get("PRICE_FEED") or "yfinance").lower().strip()
    if feed == "polygon":
        try:
            from tools.price_feed_polygon import get_quote
        except ImportError as e:
            log.warning("tools.price_feed_polygon unavailable, falling back "
                        "to yfinance: %s", e)
        else:
            q = get_quote(ticker)
            if q is not None:
                return q
            log.warning("polygon returned no quote for %s; falling back to yfinance",
                        ticker)

    yf_symbol = _resolve_yf_symbol(ticker)
    if not yf_symbol:
        return None

    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed; cannot fetch %s", ticker)
        return None

    try:
        # 5d / 1d gives us at least 2 daily bars on weekends/holidays so
        # prev_close is always derivable.
        t = yf.Ticker(yf_symbol)
        hist = t.history(period="5d", interval="1d", auto_adjust=False)
    except Exception as e:
        log.info("yfinance fetch failed for %s (%s): %s", ticker, yf_symbol, e)
        return None

    if hist is None or hist.empty or len(hist) < 1:
        log.info("yfinance returned empty history for %s (%s)", ticker, yf_symbol)
        return None

    try:
        today = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) >= 2 else None

        def _nf(v):
            try:
                f = float(v)
                return None if (f != f) else f  # filter NaN
            except (TypeError, ValueError):
                return None

        price      = _nf(today.get("Close"))
        open_      = _nf(today.get("Open"))
        high       = _nf(today.get("High"))
        low        = _nf(today.get("Low"))
        close      = _nf(today.get("Close"))
        prev_close = _nf(prev["Close"]) if prev is not None else None
        volume     = today.get("Volume")
        try:
            volume = int(volume) if volume is not None else None
        except (TypeError, ValueError):
            volume = None

        daily_change = None
        daily_change_pct = None
        if price is not None and prev_close is not None and prev_close != 0:
            daily_change = price - prev_close
            daily_change_pct = (daily_change / prev_close) * 100.0

        # snapshot_date: the date of the last bar in the frame (handles
        # weekends — Friday's bar on a Sunday lookup).
        try:
            snap_dt = today.name  # pandas Timestamp
            snapshot_date = snap_dt.date() if hasattr(snap_dt, "date") else date_cls.today()
        except Exception:
            snapshot_date = date_cls.today()

        # Compact full_payload — recent frame as list of dicts.
        try:
            full_payload = json.loads(hist.tail(5).reset_index().to_json(
                orient="records", date_format="iso"
            ))
        except Exception:
            full_payload = []

        return {
            "yf_symbol":         yf_symbol,
            "snapshot_date":     snapshot_date,
            "price":             price,
            "open":              open_,
            "high":              high,
            "low":               low,
            "close":             close,
            "prev_close":        prev_close,
            "daily_change":      daily_change,
            "daily_change_pct":  daily_change_pct,
            "volume":            volume,
            "full_payload":      full_payload,
        }
    except Exception as e:
        log.warning("yfinance row extraction failed for %s (%s): %s", ticker, yf_symbol, e)
        return None


# ─────────────────────────── Save ───────────────────────────

_VALUE_COLUMNS = (
    "price", "open", "high", "low", "close", "prev_close",
    "daily_change", "daily_change_pct", "volume",
)


def fetch_and_save(ticker: str, *, capture_type: str = "email_arrival") -> Optional[dict]:
    """Fetch + persist a yahoo_snapshots row for `ticker`.

    Idempotent on PK (ticker, snapshot_date, capture_type) — re-fetching the
    same day with the same capture_type overwrites.

    Returns the saved record dict (with snapshot_date / yf_symbol) on success,
    None on fetch or save failure. Never raises.
    """
    if not ticker:
        return None
    if capture_type not in ("email_arrival", "monitor_cycle", "on_demand"):
        log.warning("yfinance_client.fetch_and_save: unknown capture_type=%r", capture_type)
        return None

    payload = fetch_raw(ticker)
    if not payload:
        return None

    cols_sql = ", ".join(_VALUE_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_VALUE_COLUMNS))
    update_sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in _VALUE_COLUMNS)

    values = [payload.get(c) for c in _VALUE_COLUMNS]

    sql = f"""
        INSERT INTO yahoo_snapshots (
            ticker, snapshot_date, capture_type,
            {cols_sql},
            yf_symbol, full_payload, fetched_at
        )
        VALUES (
            %s, %s, %s,
            {placeholders},
            %s, %s, NOW()
        )
        ON CONFLICT (ticker, snapshot_date, capture_type) DO UPDATE SET
            {update_sets},
            yf_symbol    = EXCLUDED.yf_symbol,
            full_payload = EXCLUDED.full_payload,
            fetched_at   = NOW()
    """
    params = [
        ticker.upper(),
        payload["snapshot_date"],
        capture_type,
        *values,
        payload["yf_symbol"],
        json.dumps(payload.get("full_payload") or [], default=str),
    ]

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        # Touch inventory.last_yf_polled_at if the column exists.
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE hedgeye_ticker_inventory "
                        "   SET last_yf_polled_at = NOW() WHERE ticker = %s",
                        (ticker.upper(),),
                    )
                conn.commit()
        except Exception as e:
            log.debug("inventory last_yf_polled_at update for %s failed: %s", ticker, e)
        return payload
    except Exception as e:
        log.warning("yfinance_client.fetch_and_save(%s) save failed: %s", ticker, e)
        return None


def refresh_for_tickers(tickers: list[str], *,
                        capture_type: str = "email_arrival") -> dict:
    """Fetch + save Yahoo snapshots for each ticker. Returns a summary dict
    matching mfr_client.refresh_for_tickers' shape.

    A 0.5s per-ticker sleep paces the calls below Yahoo's per-IP rate-limit
    (warm_all_monitored 5/15 ran 314 tickers in ~13min with no pacing and
    got yf_ok=0 / yf_fail=314 despite live single-ticker fetches working).
    The sleep is skipped after the final ticker so callers feeding a single
    ticker don't pay the latency.
    """
    import time

    summary = {"tickers": 0, "ok": 0, "fail": 0}
    seen: set[str] = set()
    # Dedupe + uppercase first so the indexed loop matches the actual call count.
    cleaned: list[str] = []
    for t in tickers:
        if not t:
            continue
        sym = t.upper().strip()
        if sym in seen:
            continue
        seen.add(sym)
        cleaned.append(sym)

    last_idx = len(cleaned) - 1
    for i, sym in enumerate(cleaned):
        summary["tickers"] += 1
        try:
            res = fetch_and_save(sym, capture_type=capture_type)
            if res:
                summary["ok"] += 1
            else:
                summary["fail"] += 1
        except Exception as e:
            log.exception("yfinance refresh failed for %s: %s", sym, e)
            summary["fail"] += 1
        # Pace the next call; skip the wait on the final ticker.
        if i < last_idx:
            time.sleep(0.5)
    return summary


# ─────────────────────────── Read helpers ───────────────────────────

def latest(ticker: str) -> Optional[dict]:
    """Return the most-recent yahoo_snapshots row for `ticker`, or None."""
    if not ticker:
        return None
    sql = """
        SELECT * FROM yahoo_snapshots
         WHERE ticker = %s
         ORDER BY fetched_at DESC, snapshot_date DESC
         LIMIT 1
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (ticker.upper(),))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.warning("yfinance_client.latest(%s) failed: %s", ticker, e)
        return None


def is_stale(ticker: str, max_age_minutes: int = 30) -> bool:
    """Return True if the latest yahoo_snapshots row for ticker is missing or
    older than max_age_minutes. Default 30 min — prices move fast; an in-session
    refresh request older than that probably misses real intraday move-in.
    """
    row = latest(ticker)
    if not row:
        return True
    fetched = row.get("fetched_at")
    if not fetched:
        return True
    age_minutes = (datetime.now(fetched.tzinfo) - fetched).total_seconds() / 60.0
    return age_minutes > max_age_minutes


# ─────────────────────────── CLI smoke test ───────────────────────────

def _cli() -> None:
    ap = argparse.ArgumentParser(
        description="yfinance_client CLI: fetch+save snapshots, look up latest"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--refresh", action="store_true",
                   help="With --tickers, fetch+save snapshots for each")
    g.add_argument("--latest", metavar="TICKER",
                   help="Print the latest yahoo_snapshots row for TICKER")
    ap.add_argument("--tickers", nargs="+", help="Ticker list for --refresh")
    ap.add_argument("--capture-type", default="email_arrival",
                    choices=["email_arrival", "monitor_cycle", "on_demand"])
    args = ap.parse_args()

    if args.refresh:
        if not args.tickers:
            print("--refresh requires --tickers TICKER1 TICKER2 ...")
            return
        summary = refresh_for_tickers(args.tickers, capture_type=args.capture_type)
        print(json.dumps(summary, indent=2))
        return

    if args.latest:
        row = latest(args.latest)
        if not row:
            print(f"no rows for {args.latest!r}")
            return
        printable = {k: v for k, v in row.items() if k != "full_payload"}
        print(json.dumps(printable, indent=2, default=str))
        return


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
