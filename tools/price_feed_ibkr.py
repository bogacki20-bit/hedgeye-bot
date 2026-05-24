"""IBKR price feed via ibeam (Interactive Brokers Client Portal API).

Architecture:
  bot service  ───► ibkr-gateway (Railway service running voyz/ibeam)
                    │
                    └─► IB Client Portal Web API on localhost:5000
                          │
                          └─► IB Gateway (Java, headless, auto-logged-in)

ibeam handles daily auto-login + 2FA so the bot can treat the gateway as
an always-available HTTP service. The bot reaches it on the Railway
internal network at `http://ibkr-gateway.railway.internal:5000`
(overridable via IBKR_GATEWAY_URL).

API surface used:
  GET /v1/api/iserver/secdef/search?symbol=SPY
        ⇒ array of contracts; pick the first STK match → conid
  GET /v1/api/iserver/marketdata/snapshot?conids=756733&fields=31,82,83,7295
        ⇒ array of one dict; fields keyed by tick-type number:
              31    = Last price
              82    = Change
              83    = Change %
              7295  = Volume today
              7741  = Prior close (when subscribed)
        IBKR commonly returns empty on the first call after a cold start.
        We retry once after 800ms before declaring failure.

Conid resolution is cached in `ibkr_conid_cache` (migration 029) so
each ticker takes one secdef/search call per lifetime, not per quote.

Failure modes (all return None, never raise):
  - gateway unreachable / 5xx
  - 401 / 403 (session expired — ibeam should re-auth, but if it lags)
  - conid resolution returns no STK match
  - snapshot returns no fields after retry
The yfinance_client.fetch_raw dispatcher treats None as "fall back to
yfinance" and logs the degraded mode.

CLI smoke test:
    python -m tools.price_feed_ibkr SPY
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date as date_cls, datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

IBKR_GATEWAY_DEFAULT = "http://ibkr-gateway.railway.internal:5000"
IBKR_TIMEOUT = 10
IBKR_USER_AGENT = "HedgeyeBot/1.0"

# Tick-type fields requested on every snapshot call.
_FIELDS = "31,82,83,7295,7741"


def _gateway_url() -> str:
    return os.environ.get("IBKR_GATEWAY_URL", IBKR_GATEWAY_DEFAULT).rstrip("/")


def _get_json(path: str, params: Optional[dict] = None) -> Optional[object]:
    """GET <gateway>/<path>?... → parsed JSON, or None on any error.

    Never raises. ibeam exposes the Client Portal API at /v1/api/...; we
    pass the FULL path (including /v1/api) so the caller is explicit about
    which endpoint they want."""
    base = _gateway_url()
    if params:
        path = path + "?" + urllib.parse.urlencode(params)
    url = base + path
    req = urllib.request.Request(url, headers={"User-Agent": IBKR_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=IBKR_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        log.info("IBKR HTTP %s on %s", e.code, path)
        return None
    except Exception as e:
        log.info("IBKR gateway unreachable for %s: %s", path, e)
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        log.info("IBKR returned non-JSON for %s: %s", path, body[:200])
        return None


# ─────────────────────────── Conid resolution ──────────────────────────────

def _conid_from_cache(ticker: str) -> Optional[int]:
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT conid FROM ibkr_conid_cache WHERE ticker = %s",
                        (ticker.upper(),))
            row = cur.fetchone()
            return int(row[0]) if row else None
    except Exception as e:
        log.debug("conid cache lookup failed for %s: %s", ticker, e)
        return None


def _conid_cache_put(ticker: str, conid: int,
                     exchange: Optional[str], sec_type: Optional[str]) -> None:
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ibkr_conid_cache
                  (ticker, conid, exchange, sec_type, last_verified, created_at)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (ticker) DO UPDATE SET
                  conid         = EXCLUDED.conid,
                  exchange      = EXCLUDED.exchange,
                  sec_type      = EXCLUDED.sec_type,
                  last_verified = NOW()
                """,
                (ticker.upper(), int(conid), exchange, sec_type),
            )
            conn.commit()
    except Exception as e:
        log.warning("conid cache write failed for %s: %s", ticker, e)


def _conid_cache_touch(ticker: str) -> None:
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE ibkr_conid_cache SET last_verified = NOW() "
                        "WHERE ticker = %s", (ticker.upper(),))
            conn.commit()
    except Exception as e:
        log.debug("conid cache touch failed for %s: %s", ticker, e)


def resolve_conid(ticker: str) -> Optional[int]:
    """Map `ticker` → IBKR conid. Cached in ibkr_conid_cache.

    On a cache miss, hits /v1/api/iserver/secdef/search and picks the first
    STK / ETF match. Returns None if IBKR returns nothing useful (gateway
    down, ticker not recognized, no STK match)."""
    if not ticker:
        return None
    t = ticker.upper().strip()
    cached = _conid_from_cache(t)
    if cached is not None:
        return cached

    data = _get_json("/v1/api/iserver/secdef/search",
                     {"symbol": t, "name": "false"})
    if not isinstance(data, list) or not data:
        log.info("IBKR secdef/search returned no contracts for %s", t)
        return None

    # Prefer the first contract whose secType is STK or ETF and whose ticker
    # matches exactly. Fall back to the first row if nothing matches the
    # filter (IBKR sometimes returns the contract with secType missing).
    chosen = None
    for row in data:
        if not isinstance(row, dict):
            continue
        sym = (row.get("symbol") or "").upper()
        sec_type = (row.get("secType") or "").upper()
        if sym == t and sec_type in ("STK", "ETF", "IND"):
            chosen = row
            break
    if chosen is None:
        for row in data:
            if isinstance(row, dict) and (row.get("symbol") or "").upper() == t:
                chosen = row
                break
    if chosen is None and isinstance(data[0], dict):
        chosen = data[0]
    if not isinstance(chosen, dict):
        return None

    raw_conid = chosen.get("conid") or chosen.get("conId")
    try:
        conid = int(raw_conid)
    except (TypeError, ValueError):
        log.info("IBKR secdef row had no usable conid for %s: %s", t, chosen)
        return None

    _conid_cache_put(t, conid, chosen.get("exchange") or chosen.get("listingExchange"),
                     chosen.get("secType"))
    return conid


# ─────────────────────────── Snapshot ──────────────────────────────────────

def _parse_num(v) -> Optional[float]:
    """IBKR sometimes prefixes a number with letters ('C742.50' = closed,
    'H742.50' = halted). Strip leading alpha chars before float()."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # Trim leading non-digit/non-sign/non-dot chars
    i = 0
    while i < len(s) and s[i] not in "0123456789.-+":
        i += 1
    s = s[i:]
    try:
        return float(s)
    except ValueError:
        return None


def _snapshot(conid: int) -> Optional[dict]:
    """Pull one /marketdata/snapshot call for `conid`. Returns the raw row
    dict or None. Caller handles retry."""
    data = _get_json("/v1/api/iserver/marketdata/snapshot",
                     {"conids": str(conid), "fields": _FIELDS})
    if not isinstance(data, list) or not data:
        return None
    row = data[0] if isinstance(data[0], dict) else None
    return row


def get_quote(ticker: str) -> Optional[dict]:
    """Fetch a snapshot quote for `ticker` from the ibeam gateway.

    Returns a dict shaped the same as yfinance_client.fetch_raw / Polygon
    so the dispatcher in yfinance_client can store it transparently.
    Fields: yf_symbol, snapshot_date, price, open, high, low, close,
    prev_close, daily_change, daily_change_pct, volume, ts, source.

    Returns None on any failure — gateway unreachable, conid resolve fail,
    empty snapshot after retry, malformed payload. Caller treats None as
    "fall back to yfinance".
    """
    if not ticker:
        return None
    conid = resolve_conid(ticker)
    if conid is None:
        return None

    row = _snapshot(conid)
    # IBKR commonly returns a stub on the first call; one retry after a
    # short delay almost always populates the fields.
    if not row or not any(row.get(k) for k in ("31", "82", "83", "7295", "7741")):
        time.sleep(0.8)
        row = _snapshot(conid)
    if not row:
        log.info("IBKR snapshot empty for %s (conid=%s)", ticker, conid)
        return None

    last       = _parse_num(row.get("31"))
    change     = _parse_num(row.get("82"))
    change_pct = _parse_num(row.get("83"))
    volume     = _parse_num(row.get("7295"))
    prior      = _parse_num(row.get("7741"))

    if last is None:
        log.info("IBKR snapshot for %s missing last price (conid=%s); "
                 "raw=%s", ticker, conid, {k: row.get(k) for k in ("31","82","83","7295","7741")})
        return None

    # Derive prev_close: prefer field 7741 when populated, else (last - change).
    prev_close = prior
    if prev_close is None and change is not None:
        prev_close = last - change

    try:
        volume_int = int(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume_int = None

    ts_ms = int(time.time() * 1000)
    _conid_cache_touch(ticker)

    return {
        "yf_symbol":         ticker.upper(),   # name kept for upstream shape
        "snapshot_date":     date_cls.today(),
        "price":             last,
        "open":              None,             # snapshot endpoint doesn't carry OHL
        "high":              None,
        "low":               None,
        "close":             last,
        "prev_close":        prev_close,
        "daily_change":      change,
        "daily_change_pct":  change_pct,
        "volume":            volume_int,
        "ts":                ts_ms,
        "source":            "ibkr",
        "full_payload":      [row],
    }


# ─────────────────────────── Gateway healthcheck ───────────────────────────

def is_authenticated() -> Optional[bool]:
    """True if the ibeam gateway reports an authenticated session, False
    if explicitly unauthenticated, None if the gateway is unreachable.

    /v1/api/iserver/auth/status returns {"authenticated": true, "competing": false,
    "connected": true, ...}. Useful for the bot's startup smoke + health
    endpoints."""
    data = _get_json("/v1/api/iserver/auth/status")
    if not isinstance(data, dict):
        return None
    return bool(data.get("authenticated"))


# ─────────────────────────── CLI ───────────────────────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.price_feed_ibkr")
    ap.add_argument("ticker", help="Ticker to fetch (e.g. SPY)")
    ap.add_argument("--healthcheck", action="store_true",
                    help="Print the gateway auth status and exit")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.healthcheck:
        s = is_authenticated()
        print(f"ibeam gateway authenticated: {s}")
        return 0 if s else 1

    q = get_quote(a.ticker)
    if q is None:
        print("(no IBKR quote — gateway unreachable, conid resolution failed, "
              "or snapshot empty. Upstream caller will fall back to yfinance.)")
        return 1
    print(json.dumps(q, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
