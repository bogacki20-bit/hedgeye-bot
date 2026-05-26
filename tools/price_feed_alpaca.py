"""Alpaca price feed (REST) behind the PRICE_FEED flag.

Wired in 2026-05-25 as the operator's replacement for the parked IBKR
gateway path. Alpaca's free tier (IEX feed) covers our ≤ 15-min cadence
without a headless Java gateway, daily auto-login, or 2FA dance.

Endpoints (via alpaca-py SDK):
  equities/ETFs:
    StockHistoricalDataClient.get_stock_latest_quote(...)  → bid/ask + ts
    StockHistoricalDataClient.get_stock_latest_bar(...)    → today's OHLCV
  crypto:
    CryptoHistoricalDataClient.get_crypto_latest_quote(...)
    CryptoHistoricalDataClient.get_crypto_latest_bar(...)

`get_quote(ticker)` normalizes the response to the same shape every other
price feed in this repo returns (yfinance_client.fetch_raw,
tools.price_feed_polygon, tools.price_feed_ibkr) so the dispatcher swap
in yfinance_client.py is a one-liner.

Feed selection: IEX first (free tier). On `APIError` we retry once with
SIP — paid subscriptions fall through, free accounts will see SIP fail
and bubble out as None so the dispatcher cascades to yfinance.

Crypto ticker translation: Alpaca uses pair notation (BTC/USD). We map
the Hedgeye/proxy labels (BITCOIN, IBIT, ETHE) to pairs via
_CRYPTO_TICKER_MAP at the top.

Auth: ALPACA_API_KEY + ALPACA_API_SECRET. Base URL defaults to paper
(https://paper-api.alpaca.markets); set ALPACA_BASE_URL=https://api.alpaca.markets
to point at live. Market data endpoints don't actually consult the base
URL — only the trading API does — but we forward it to the trading
client for consistency and surface it in logs.

Failure modes (all return None, never raise):
  - missing key/secret
  - alpaca-py not installed
  - API error on both IEX and SIP feeds
  - ticker rejected (unknown symbol)
  - malformed payload

CLI smoke test:
    ALPACA_API_KEY=... ALPACA_API_SECRET=... python -m tools.price_feed_alpaca SPY
    ALPACA_API_KEY=... ALPACA_API_SECRET=... python -m tools.price_feed_alpaca BITCOIN
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import date as date_cls
from typing import Optional

log = logging.getLogger(__name__)


# ─────────────────────────── Ticker translation ───────────────────────────

# Hedgeye / proxy label → Alpaca crypto pair.
# Keep tight; equity proxies (IBIT, ETHE, GBTC) stay on the STOCK path because
# they're spot ETFs traded as equities — Alpaca quotes them as stocks.
_CRYPTO_TICKER_MAP: dict[str, str] = {
    "BITCOIN":  "BTC/USD",
    "BTC":      "BTC/USD",
    "BTCUSD":   "BTC/USD",
    "ETHEREUM": "ETH/USD",
    "ETH":      "ETH/USD",
    "ETHUSD":   "ETH/USD",
    "SOLANA":   "SOL/USD",
    "SOL":      "SOL/USD",
}


def _resolve_credentials() -> tuple[Optional[str], Optional[str]]:
    return (os.environ.get("ALPACA_API_KEY"),
            os.environ.get("ALPACA_API_SECRET"))


def _is_crypto(ticker: str) -> Optional[str]:
    """Return the Alpaca pair if `ticker` is a crypto label, else None."""
    if not ticker:
        return None
    return _CRYPTO_TICKER_MAP.get(ticker.upper())


# ─────────────────────────── Equity / ETF quote ───────────────────────────

def _quote_equity(ticker: str, key: str, secret: str) -> Optional[dict]:
    """Fetch latest quote + latest bar for an equity/ETF via alpaca-py.
    Returns the normalized dict shape, or None on any failure.

    Two REST calls: latest_quote (for bid/ask + timestamp) and latest_bar
    (for today's OHLCV + volume). Prev-close stays None — Alpaca's
    latest endpoints don't carry yesterday's close; if downstream needs
    it, a follow-up historical-bars call would do it.
    """
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import (
            StockLatestQuoteRequest, StockLatestBarRequest,
        )
        from alpaca.data.enums import DataFeed
    except ImportError as e:
        log.warning("alpaca-py not installed; cannot fetch %s: %s", ticker, e)
        return None

    client = StockHistoricalDataClient(api_key=key, secret_key=secret)
    sym = ticker.upper().strip()

    # Try IEX (free) then SIP (paid). The exception classes vary across
    # alpaca-py versions; catch broadly and inspect.
    def _try_feed(feed):
        try:
            qreq = StockLatestQuoteRequest(symbol_or_symbols=sym, feed=feed)
            q = client.get_stock_latest_quote(qreq).get(sym)
            breq = StockLatestBarRequest(symbol_or_symbols=sym, feed=feed)
            b = client.get_stock_latest_bar(breq).get(sym)
            return q, b
        except Exception as ex:
            log.info("Alpaca %s fetch for %s failed: %s", feed.value, sym, ex)
            return None, None

    quote, bar = _try_feed(DataFeed.IEX)
    if quote is None and bar is None:
        quote, bar = _try_feed(DataFeed.SIP)
    if quote is None and bar is None:
        return None

    # Mid of bid/ask is the closest thing to a "current price" the quote
    # endpoint exposes. Fall back to bar.close, then to ask, then to bid.
    price: Optional[float] = None
    bid = getattr(quote, "bid_price", None) if quote else None
    ask = getattr(quote, "ask_price", None) if quote else None
    try:
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            price = (float(bid) + float(ask)) / 2.0
    except (TypeError, ValueError):
        price = None
    if price is None and bar is not None:
        try:
            price = float(getattr(bar, "close", None))
        except (TypeError, ValueError):
            price = None
    if price is None and ask:
        try:
            price = float(ask)
        except (TypeError, ValueError):
            price = None
    if price is None and bid:
        try:
            price = float(bid)
        except (TypeError, ValueError):
            price = None
    if price is None:
        log.info("Alpaca returned no usable price for %s", sym)
        return None

    # Bar-derived fields.
    def _nf(v):
        try:
            f = float(v)
            return None if (f != f) else f
        except (TypeError, ValueError):
            return None

    open_ = _nf(getattr(bar, "open", None))  if bar else None
    high  = _nf(getattr(bar, "high", None))  if bar else None
    low   = _nf(getattr(bar, "low", None))   if bar else None
    close = _nf(getattr(bar, "close", None)) if bar else None
    vol_raw = getattr(bar, "volume", None)   if bar else None
    try:
        volume = int(vol_raw) if vol_raw is not None else None
    except (TypeError, ValueError):
        volume = None

    # Timestamp — prefer quote.timestamp, fall back to bar.timestamp.
    ts_ms: Optional[int] = None
    for src in (quote, bar):
        ts_attr = getattr(src, "timestamp", None) if src else None
        if ts_attr is not None:
            try:
                # alpaca-py returns datetime objects.
                ts_ms = int(ts_attr.timestamp() * 1000)
                break
            except Exception:
                continue
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)

    return {
        "yf_symbol":         sym,
        "snapshot_date":     date_cls.today(),
        "price":             price,
        "open":              open_,
        "high":              high,
        "low":               low,
        "close":             close,
        "prev_close":        None,   # Alpaca latest endpoints don't carry it
        "daily_change":      None,
        "daily_change_pct":  None,
        "volume":            volume,
        "ts":                ts_ms,
        "source":            "alpaca",
        "full_payload":      [{
            "quote": _model_to_dict(quote),
            "bar":   _model_to_dict(bar),
        }],
    }


# ─────────────────────────── Crypto quote ───────────────────────────

def _quote_crypto(pair: str, key: str, secret: str) -> Optional[dict]:
    """Fetch latest quote + latest bar for a crypto pair (e.g. BTC/USD)."""
    try:
        from alpaca.data.historical import CryptoHistoricalDataClient
        from alpaca.data.requests import (
            CryptoLatestQuoteRequest, CryptoLatestBarRequest,
        )
    except ImportError as e:
        log.warning("alpaca-py not installed; cannot fetch crypto %s: %s", pair, e)
        return None

    # Crypto client doesn't require auth for public market data, but pass keys
    # anyway so rate limits land against the user's account.
    client = CryptoHistoricalDataClient(api_key=key, secret_key=secret)

    try:
        qreq = CryptoLatestQuoteRequest(symbol_or_symbols=pair)
        quote = client.get_crypto_latest_quote(qreq).get(pair)
        breq = CryptoLatestBarRequest(symbol_or_symbols=pair)
        bar  = client.get_crypto_latest_bar(breq).get(pair)
    except Exception as ex:
        log.info("Alpaca crypto fetch for %s failed: %s", pair, ex)
        return None

    if quote is None and bar is None:
        return None

    price: Optional[float] = None
    bid = getattr(quote, "bid_price", None) if quote else None
    ask = getattr(quote, "ask_price", None) if quote else None
    try:
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            price = (float(bid) + float(ask)) / 2.0
    except (TypeError, ValueError):
        price = None
    if price is None and bar is not None:
        try:
            price = float(getattr(bar, "close", None))
        except (TypeError, ValueError):
            price = None
    if price is None:
        log.info("Alpaca crypto returned no usable price for %s", pair)
        return None

    def _nf(v):
        try:
            f = float(v)
            return None if (f != f) else f
        except (TypeError, ValueError):
            return None

    open_ = _nf(getattr(bar, "open", None))  if bar else None
    high  = _nf(getattr(bar, "high", None))  if bar else None
    low   = _nf(getattr(bar, "low", None))   if bar else None
    close = _nf(getattr(bar, "close", None)) if bar else None
    vol_raw = getattr(bar, "volume", None)   if bar else None
    try:
        volume = int(float(vol_raw)) if vol_raw is not None else None
    except (TypeError, ValueError):
        volume = None

    ts_ms: Optional[int] = None
    for src in (quote, bar):
        ts_attr = getattr(src, "timestamp", None) if src else None
        if ts_attr is not None:
            try:
                ts_ms = int(ts_attr.timestamp() * 1000)
                break
            except Exception:
                continue
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)

    return {
        "yf_symbol":         pair,
        "snapshot_date":     date_cls.today(),
        "price":             price,
        "open":              open_,
        "high":              high,
        "low":               low,
        "close":             close,
        "prev_close":        None,
        "daily_change":      None,
        "daily_change_pct":  None,
        "volume":            volume,
        "ts":                ts_ms,
        "source":            "alpaca",
        "full_payload":      [{
            "quote": _model_to_dict(quote),
            "bar":   _model_to_dict(bar),
        }],
    }


# ─────────────────────────── Public entry ───────────────────────────

def get_quote(ticker: str) -> Optional[dict]:
    """Fetch a price snapshot for `ticker` from Alpaca.

    Returns the same dict shape as yfinance_client.fetch_raw / Polygon /
    IBKR feeds — yf_symbol, snapshot_date, price, open, high, low, close,
    prev_close, daily_change, daily_change_pct, volume, ts, source,
    full_payload.

    Returns None on any failure (missing creds, alpaca-py missing,
    network error, unknown symbol, malformed response). The dispatcher
    in yfinance_client treats None as "fall back to yfinance".
    """
    if not ticker:
        return None
    key, secret = _resolve_credentials()
    if not key or not secret:
        log.warning("ALPACA_API_KEY / ALPACA_API_SECRET not set; cannot fetch %s "
                    "via Alpaca", ticker)
        return None

    pair = _is_crypto(ticker)
    if pair:
        return _quote_crypto(pair, key, secret)
    return _quote_equity(ticker, key, secret)


# ─────────────────────────── Helpers ───────────────────────────

def _model_to_dict(m) -> dict:
    """Best-effort serialize an alpaca-py pydantic model for full_payload."""
    if m is None:
        return {}
    for attr in ("model_dump", "dict"):
        f = getattr(m, attr, None)
        if callable(f):
            try:
                return f()
            except Exception:
                pass
    try:
        return dict(m.__dict__)
    except Exception:
        return {"_repr": repr(m)}


# ─────────────────────────── CLI ───────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.price_feed_alpaca")
    ap.add_argument("ticker", help="Ticker symbol (e.g. SPY, BITCOIN)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    q = get_quote(a.ticker)
    if q is None:
        print("(no Alpaca quote — missing creds, alpaca-py not installed, "
              "API error, or unknown symbol)")
        return 1
    print(json.dumps(q, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
