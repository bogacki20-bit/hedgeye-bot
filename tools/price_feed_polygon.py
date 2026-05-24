"""Polygon.io price feed — stub implementation behind the PRICE_FEED flag.

Wired in 2026-05-24 as the future replacement for yfinance. Default
PRICE_FEED=yfinance keeps the bot on the existing path until POLYGON_API_KEY
lands.

Endpoint: GET /v2/aggs/ticker/{ticker}/prev
Docs:     https://polygon.io/docs/stocks/get_v2_aggs_ticker__stocksticker__prev

The previous-close aggregate returns one bar:
  c  close
  h  high
  l  low
  o  open
  v  volume
  vw VWAP
  t  Unix ms timestamp

`get_quote(ticker)` normalizes that to the shape `yfinance_client.fetch_raw`
returns so the swap is a one-liner upstream.

CLI smoke test:
    POLYGON_API_KEY=... python -m tools.price_feed_polygon AAPL
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date as date_cls
from typing import Optional

log = logging.getLogger(__name__)

POLYGON_BASE = "https://api.polygon.io"
POLYGON_TIMEOUT = 20


def _resolve_key() -> Optional[str]:
    return os.environ.get("POLYGON_API_KEY")


def get_quote(ticker: str) -> Optional[dict]:
    """Fetch the previous-trading-day OHLCV bar for `ticker` from Polygon.

    Returns a dict matching the shape of `yfinance_client.fetch_raw`:
      yf_symbol, snapshot_date, price, open, high, low, close, prev_close,
      daily_change, daily_change_pct, volume, ts, full_payload.

    Returns None on missing key, 404, malformed payload, or network error.
    Never raises.
    """
    key = _resolve_key()
    if not key:
        log.warning("POLYGON_API_KEY not set; cannot fetch %s via Polygon", ticker)
        return None

    if not ticker:
        return None
    sym = urllib.parse.quote(ticker.upper(), safe="")
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{sym}/prev?adjusted=true&apiKey={key}"
    req = urllib.request.Request(url, headers={"User-Agent": "HedgeyeBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=POLYGON_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        log.info("Polygon HTTP %s for %s", e.code, ticker)
        return None
    except Exception as e:
        log.warning("Polygon fetch failed for %s: %s", ticker, e)
        return None

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        log.warning("Polygon JSON parse failed for %s: %s", ticker, e)
        return None

    results = payload.get("results") or []
    if not results:
        log.info("Polygon returned no results for %s (resultsCount=%s)",
                 ticker, payload.get("resultsCount"))
        return None
    row = results[0]

    def _nf(v):
        try:
            f = float(v)
            return None if (f != f) else f
        except (TypeError, ValueError):
            return None

    price      = _nf(row.get("c"))
    open_      = _nf(row.get("o"))
    high       = _nf(row.get("h"))
    low        = _nf(row.get("l"))
    close      = _nf(row.get("c"))
    prev_close = None  # /prev is a single bar — caller-provided history needed for diff
    volume     = row.get("v")
    try:
        volume = int(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume = None

    # Unix ms → date.
    ts = row.get("t")
    snapshot_date: date_cls
    try:
        from datetime import datetime as _dt, timezone as _tz
        snapshot_date = _dt.fromtimestamp(ts / 1000.0, tz=_tz.utc).date() if ts else date_cls.today()
    except Exception:
        snapshot_date = date_cls.today()

    return {
        "yf_symbol":         ticker.upper(),   # name kept for upstream shape
        "snapshot_date":     snapshot_date,
        "price":             price,
        "open":              open_,
        "high":              high,
        "low":               low,
        "close":             close,
        "prev_close":        prev_close,
        "daily_change":      None,             # /prev doesn't carry prior-day for diff
        "daily_change_pct":  None,
        "volume":            volume,
        "ts":                ts,
        "full_payload":      [row],
    }


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.price_feed_polygon")
    ap.add_argument("ticker", help="Ticker symbol (e.g. AAPL)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    q = get_quote(a.ticker)
    if q is None:
        print("(no quote returned)")
        return 1
    print(json.dumps(q, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
