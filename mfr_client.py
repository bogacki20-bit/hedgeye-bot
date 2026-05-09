"""MyFractalRange (MFR) API client — fetch per-ticker risk-range snapshots.

The MFR Elite Monthly subscription provides a JWT-authenticated REST endpoint:
    GET https://myfractalrange.com/v2/asset/{TICKER}?token={MFR_API_TOKEN}

Returns the daily Hurst exponent, IV/RV, momentum and trend signals, and the
range_low / range_high for the ticker. We snapshot one row per (ticker, date)
into the `mfr_snapshots` table so the bot can reference Bruce's framework
alongside Hedgeye's Risk Range and SpotGamma's gamma levels.

Usage:
    from mfr_client import fetch_and_save
    payload = fetch_and_save("OIH")
    if payload:
        print(payload["range_low"], payload["range_high"])

The MFR_API_TOKEN env var must be set (Railway has it; locally it's in .env).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date, datetime
from typing import Any

log = logging.getLogger(__name__)

MFR_BASE = "https://myfractalrange.com/v2/asset"
MFR_USER_AGENT = "Mozilla/5.0 (HedgeyeBot/1.0)"  # MFR returns 403 to default urllib UA
MFR_TIMEOUT = 20


def _resolve_token() -> str | None:
    return os.environ.get("MFR_API_TOKEN") or os.environ.get("MFR_TOKEN")


def fetch_raw(ticker: str) -> dict | None:
    """GET the MFR API and return the parsed JSON, or None on failure.

    Tries the ticker as-given; if 404, retries with common variants (e.g.
    "BITCOIN" -> "BTCUSD"). Returns None if all variants fail.
    """
    token = _resolve_token()
    if not token:
        log.warning("MFR_API_TOKEN not set; skipping MFR fetch for %s", ticker)
        return None

    candidates = [ticker]
    # Common MFR aliasing — extend as we discover more
    aliases = {
        "BITCOIN": ["BTCUSD", "BTC"],
        "WTIC":    ["WTI", "CRUDE"],
        "BRENT":   ["BRENTOIL", "BRN"],
        "GOLD":    ["XAUUSD", "GOLD_FUT"],
        "SILVER":  ["XAGUSD"],
        "COPPER":  ["HG", "COPPER_FUT"],
        "NATGAS":  ["NG", "NATURALGAS"],
        "EUR/USD": ["EURUSD"],
        "GBP/USD": ["GBPUSD"],
        "USD/YEN": ["USDJPY"],
        "CAD/USD": ["USDCAD"],
        "USD":     ["DXY"],
        "VIX":     ["VIXIDX"],
    }
    if ticker in aliases:
        candidates.extend(aliases[ticker])

    last_err: str | None = None
    for cand in candidates:
        url = f"{MFR_BASE}/{cand}?token={token}"
        req = urllib.request.Request(url, headers={"User-Agent": MFR_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=MFR_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                last_err = f"non-json response: {body[:200]}"
                continue
            # MFR returns {"success": true, "data": {...}} or {"success": false}
            if isinstance(data, dict) and data.get("success") is False:
                last_err = f"success=false ({cand})"
                continue
            payload = data.get("data") if isinstance(data, dict) and "data" in data else data
            if not isinstance(payload, dict):
                last_err = "unexpected shape"
                continue
            payload.setdefault("_mfr_ticker_used", cand)
            return payload
        except urllib.error.HTTPError as e:
            last_err = f"http {e.code} ({cand})"
            if e.code in (401, 403):
                # Auth error means token is wrong; no point trying other variants.
                log.error("MFR auth error for %s: %s", cand, e)
                return None
            continue
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue

    log.info("MFR fetch failed for %s after %d candidate(s); last error: %s",
             ticker, len(candidates), last_err)
    return None


def _flatten_for_save(payload: dict) -> dict:
    """Pull canonical fields out of the MFR payload for the typed columns
    in `mfr_snapshots`. Anything not extracted still goes into full_payload.
    """
    out: dict[str, Any] = {}

    def _try(*keys):
        for k in keys:
            if k in payload and payload[k] is not None:
                return payload[k]
        return None

    out["price"]               = _try("price", "last", "last_price", "close")
    out["range_low"]           = _try("range_low", "rangeLow", "low")
    out["range_high"]          = _try("range_high", "rangeHigh", "high")
    out["trend_signal"]        = _try("trend_signal", "trendSignal", "trend")
    out["momentum_signal"]     = _try("momentum_signal", "momentumSignal", "momentum")
    out["hurst"]               = _try("hurst", "hurst_daily")
    out["hurst_3mo"]           = _try("hurst_3mo", "hurst3mo")
    out["iv"]                  = _try("iv", "implied_vol")
    out["rv"]                  = _try("rv", "realized_vol")
    out["daily_pct_change"]    = _try("daily_pct_change", "dailyPctChange", "pct_change")
    out["previous_day_volume"] = _try("previous_day_volume", "previousDayVolume", "volume")
    return out


def fetch_and_save(ticker: str, snapshot_date: date | None = None) -> dict | None:
    """Fetch MFR for a ticker and persist to mfr_snapshots. Returns the saved
    payload dict, or None if fetch failed.

    Idempotent: PRIMARY KEY (ticker, snapshot_date) on the table — re-running
    the same day overwrites via ON CONFLICT in save_mfr_snapshot.
    """
    payload = fetch_raw(ticker)
    if not payload:
        return None
    if snapshot_date is None:
        snapshot_date = date.today()
    flat = _flatten_for_save(payload)

    try:
        # Lazy import so this module can be imported without psycopg2.
        import db_pg
        db_pg.save_mfr_snapshot(ticker, snapshot_date, payload, flat)
        # Also update the inventory table's last_mfr_fetched_at if available.
        try:
            with db_pg.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE hedgeye_ticker_inventory
                        SET last_mfr_fetched_at = NOW()
                        WHERE ticker = %s
                        """,
                        (ticker,),
                    )
                conn.commit()
        except Exception as e:
            # Inventory table may not exist yet (migration 004 not applied).
            log.debug("could not update inventory.last_mfr_fetched_at for %s: %s", ticker, e)
        return payload
    except Exception as e:
        log.warning("MFR save failed for %s: %s", ticker, e)
        return None


def refresh_for_tickers(tickers: list[str]) -> dict:
    """Fetch MFR for each ticker and save. Returns a summary dict.

    Used by the email parser after extracting tickers from a Hedgeye
    signal — refresh MFR for every mentioned ticker so the corpus is fresh
    when the bot reasons over the alert.
    """
    summary = {"tickers": len(tickers), "ok": 0, "skip": 0, "fail": 0}
    seen = set()
    for t in tickers:
        if not t or t in seen:
            continue
        seen.add(t)
        try:
            res = fetch_and_save(t)
            if res:
                summary["ok"] += 1
            else:
                summary["fail"] += 1
        except Exception as e:
            log.exception("MFR refresh failed for %s: %s", t, e)
            summary["fail"] += 1
    return summary
