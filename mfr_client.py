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
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any

log = logging.getLogger(__name__)

MFR_BASE = "https://myfractalrange.com/v2/asset"
MFR_USER_AGENT = "Mozilla/5.0 (HedgeyeBot/1.0)"  # MFR returns 403 to default urllib UA
MFR_TIMEOUT = 20


def _resolve_token() -> str | None:
    return os.environ.get("MFR_API_TOKEN") or os.environ.get("MFR_TOKEN")


# ─────────────────────────── Watchlist (fan-out source of truth) ───────────

def list_watchlist() -> list[str]:
    """Return the tickers in the operator's MFR account (`createdBy` =
    whoever the MFR_API_TOKEN belongs to).

    Endpoint: GET /v2/asset (no ticker path component). Returns
    {"data": [{"payload": {"ticker": ...}}, ...]}.

    This is the canonical source of truth for which tickers the bot should
    fetch MFR snapshots for. When the operator adds a ticker through the
    MFR UI, it appears here within one API call — no code change required.

    Returns sorted distinct ticker symbols. Empty list on auth error / API
    down / malformed payload. Never raises.
    """
    token = _resolve_token()
    if not token:
        log.warning("MFR_API_TOKEN not set; cannot list watchlist")
        return []
    url = f"{MFR_BASE}?token={token}"
    req = urllib.request.Request(url, headers={"User-Agent": MFR_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=MFR_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("MFR watchlist fetch failed: %s", e)
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        log.warning("MFR watchlist returned non-JSON: %s", body[:200])
        return []
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        log.warning("MFR watchlist unexpected shape: %s", str(data)[:200])
        return []
    out: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        p = it.get("payload") or {}
        t = p.get("ticker")
        if t and isinstance(t, str):
            out.add(t.strip().upper())
    return sorted(out)


def refresh_watchlist() -> dict:
    """Pull the operator's MFR watchlist and fetch+save a snapshot for
    every ticker. Logs `mfr_fanout: pulled N tickers from MFR watchlist`.

    Idempotent — `fetch_and_save` upserts on (ticker, snapshot_date) so
    re-running on the same day overwrites without duplicating rows.
    Returns the same shape as `refresh_for_tickers`."""
    watchlist = list_watchlist()
    log.info("mfr_fanout: pulled %d tickers from MFR watchlist", len(watchlist))
    if not watchlist:
        return {"tickers": 0, "ok": 0, "skip": 0, "fail": 0, "source": "mfr_watchlist"}
    summary = refresh_for_tickers(watchlist)
    summary["source"] = "mfr_watchlist"
    log.info("mfr_fanout: refresh complete — ok=%d fail=%d (of %d)",
             summary.get("ok", 0), summary.get("fail", 0), summary.get("tickers", 0))
    return summary


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
    # Common MFR aliasing — extend as we discover more.
    # NB: Hedgeye Macro Show TL;DR uses naked currency codes (GBP, CAD, USD)
    # while MFR's symbol set is mixed: some entries use FX pair codes (EURUSD),
    # some use ICE/futures dollar-index codes (DXY), and some use the WisdomTree
    # currency ETF tickers (FXC, FXB, etc.). Try multiple variants per code.
    aliases = {
        # Crypto
        "BITCOIN": ["BTCUSD", "BTC"],
        "BTC":     ["BTCUSD"],
        "ETH":     ["ETHUSD"],
        "ETHEREUM":["ETHUSD"],

        # Commodities
        "WTIC":    ["WTI", "CRUDE"],
        "BRENT":   ["BRENTOIL", "BRN"],
        "GOLD":    ["XAUUSD", "GOLD_FUT"],
        "SILVER":  ["XAGUSD"],
        "COPPER":  ["HG", "COPPER_FUT"],
        "NATGAS":  ["NG", "NATURALGAS"],

        # Currencies — MFR canonical confirmed by user 2026-05-11:
        #   USD -> "UUP" (Invesco DB Dollar Bullish ETF, MFR's stand-in for the index)
        #   CAD -> "USD/CAD" (MFR uses standard FX-pair notation;
        #                     "crosses" put USD first, "majors" put USD second)
        # Slash-form is the primary; concat / inverse / ETF kept as fallbacks.
        # Pair conventions follow market standard:
        #   EUR/GBP/AUD/NZD -> USD second (EUR/USD, GBP/USD, ...)
        #   JPY/CAD/CHF/MXN/CNY -> USD first (USD/JPY, USD/CAD, ...)
        "USD":     ["UUP", "DXY"],
        "EUR":     ["EUR/USD", "EURUSD", "FXE"],
        "GBP":     ["GBP/USD", "GBPUSD", "FXB"],
        "AUD":     ["AUD/USD", "AUDUSD", "FXA"],
        "NZD":     ["NZD/USD", "NZDUSD", "BNZ"],
        "JPY":     ["USD/JPY", "USDJPY", "FXY"],
        "CAD":     ["USDCAD", "USD/CAD", "FXC"],
        "CHF":     ["USD/CHF", "USDCHF", "FXF"],
        "MXN":     ["USD/MXN", "USDMXN", "FXM"],
        "CNY":     ["USD/CNY", "USDCNY", "CYB"],
        # Slash forms — kept for any caller still using them
        "EUR/USD": ["EURUSD"],
        "GBP/USD": ["GBPUSD"],
        "USD/YEN": ["USDJPY"],
        "USD/JPY": ["USDJPY"],
        "CAD/USD": ["USDCAD"],
        "USD/CAD": ["USDCAD"],
        "AUD/USD": ["AUDUSD"],

        # Vol
        "VIX":     ["VIXIDX"],
    }
    if ticker in aliases:
        candidates.extend(aliases[ticker])

    last_err: str | None = None
    for cand in candidates:
        # URL-quote special characters but keep "/" literal: MFR's routing
        # accepts FX-pair paths like /v2/asset/CAD/USD (the slash IS the
        # pair separator, not an encoded sub-path).
        cand_quoted = urllib.parse.quote(cand, safe='/')
        url = f"{MFR_BASE}/{cand_quoted}?token={token}"
        req = urllib.request.Request(url, headers={"User-Agent": MFR_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=MFR_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                last_err = f"non-json response: {body[:200]}"
                continue
            # MFR returns either {"success": false} (legacy) or — current
            # production shape — {"id": "...", "payload": {ticker, latestPrice,
            # rangeData, trendSignal, ...}}. Older code expected {"data": {...}}.
            # Unwrap whichever envelope is present so downstream extractors see
            # the asset fields at the top level.
            if isinstance(data, dict) and data.get("success") is False:
                last_err = f"success=false ({cand})"
                continue
            # The MFR API double-wraps: {"data": {"id": ..., "payload": {asset_fields}}}.
            # Peel both levels so downstream extractors see latestPrice, hurst,
            # rangeData etc at the top level. Tolerant of single-wrap legacy
            # shapes too.
            payload = data
            if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], dict):
                payload = payload["data"]
            if isinstance(payload, dict) and "payload" in payload and isinstance(payload["payload"], dict):
                payload = payload["payload"]
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
    in `mfr_snapshots` (and our corpus markdown body). Anything not
    extracted still goes into full_payload.

    Key names match the actual API shape (camelCase), with snake_case
    fallbacks kept for forward compatibility if MFR ever changes shape.
    """
    out: dict[str, Any] = {}

    def _try(*keys):
        for k in keys:
            if k in payload and payload[k] is not None:
                return payload[k]
        return None

    range_data = (payload.get("rangeData") or {}) if isinstance(payload, dict) else {}
    lt_range_data = (payload.get("ltRangeData") or {}) if isinstance(payload, dict) else {}

    out["price"]               = _try("latestPrice", "price", "last", "last_price", "close")
    out["range_low"]           = (
        range_data.get("lowerRange")
        or _try("range_low", "rangeLow", "low")
    )
    out["range_high"]          = (
        range_data.get("upperRange")
        or _try("range_high", "rangeHigh", "high")
    )
    out["lt_range_low"]        = lt_range_data.get("lowerRange")
    out["lt_range_high"]       = lt_range_data.get("upperRange")
    out["trend_signal"]        = _try("trendSignal", "trend_signal", "trend")
    out["momentum_signal"]     = _try("momentumSignal", "momentum_signal", "momentum")
    out["hurst"]               = _try("hurst", "hurst_daily")
    out["hurst_3mo"]           = _try("hurst3Mo", "hurst_3mo", "hurst3mo")
    out["iv"]                  = _try("iv", "implied_vol")
    out["rv"]                  = _try("rv", "realized_vol")
    out["daily_pct_change"]    = _try("dailyPercentChange", "daily_pct_change", "dailyPctChange", "pct_change")
    out["previous_day_volume"] = _try("previousDayVolume", "previous_day_volume", "volume")
    out["name"]                = _try("name")
    out["sector"]              = _try("sector")
    out["industry"]            = _try("industry")
    out["latest_range_date"]   = _try("latestRangeDate", "latest_range_date")
    return out


def _format_corpus_markdown(ticker: str, snapshot_date: date, flat: dict, payload: dict) -> str:
    """Build a human-readable markdown body for the corpus_documents row.

    Includes the typed fields (range, hurst, IV/RV, momentum, trend) plus a
    raw JSON appendix so anything not in `flat` is still searchable via FTS.
    """
    def _fmt(v, suffix=""):
        if v is None:
            return "—"
        try:
            return f"{float(v):.4f}{suffix}"
        except (TypeError, ValueError):
            return str(v)

    lines = [
        f"# MFR Snapshot — {ticker} ({snapshot_date.isoformat()})",
        "",
        f"Source: MyFractalRange API (`https://myfractalrange.com/v2/asset/{payload.get('_mfr_ticker_used', ticker)}`)",
        "",
        "## Asset",
        "",
        f"- **Name:** {flat.get('name') or '—'}",
        f"- **Sector:** {flat.get('sector') or '—'}",
        f"- **Industry:** {flat.get('industry') or '—'}",
        f"- **Latest range date:** {flat.get('latest_range_date') or '—'}",
        "",
        "## Daily Range & Trend",
        "",
        f"- **Range Low:**  {_fmt(flat.get('range_low'))}",
        f"- **Range High:** {_fmt(flat.get('range_high'))}",
        f"- **Price:**      {_fmt(flat.get('price'))}",
        f"- **Trend Signal:**    {flat.get('trend_signal') or '—'}",
        f"- **Momentum Signal:** {flat.get('momentum_signal') or '—'}",
        "",
        "## Long-Term Range",
        "",
        f"- **LT Range Low:**  {_fmt(flat.get('lt_range_low'))}",
        f"- **LT Range High:** {_fmt(flat.get('lt_range_high'))}",
        "",
        "## Volatility & Statistics",
        "",
        f"- **Hurst (daily):** {_fmt(flat.get('hurst'))}",
        f"- **Hurst (3-month):** {_fmt(flat.get('hurst_3mo'))}",
        f"- **Implied Vol (IV):** {_fmt(flat.get('iv'), '%')}",
        f"- **Realized Vol (RV):** {_fmt(flat.get('rv'), '%')}",
        f"- **Daily % Change:** {_fmt(flat.get('daily_pct_change'), '%')}",
        f"- **Previous Day Volume:** {flat.get('previous_day_volume') or '—'}",
        "",
        "## Raw payload",
        "",
        "```json",
        json.dumps(payload, indent=2, default=str)[:4000],  # cap to keep FTS index sane
        "```",
    ]
    return "\n".join(lines)


def _save_to_corpus(ticker: str, snapshot_date: date, flat: dict, payload: dict) -> bool:
    """Upsert an MFR snapshot into corpus_documents so it's searchable via FTS
    alongside Hedgeye/SpotGamma/VolStudies content. Uses ON CONFLICT DO UPDATE
    so re-fetching the same (ticker, date) overwrites stale text — opposite
    of corpus_ingest's DO NOTHING semantics, which is correct for MFR since
    the API returns refined values intra-day.

    Returns True on success, False on any error. Never raises.
    """
    try:
        import db_pg
        body = _format_corpus_markdown(ticker, snapshot_date, flat, payload)
        title = f"{ticker} MFR snapshot {snapshot_date.isoformat()}"
        # Compact metadata: the typed `flat` fields plus the MFR ticker
        # variant actually used (handles aliases like BITCOIN -> BTCUSD).
        metadata = {
            **{k: v for k, v in flat.items() if v is not None},
            "_mfr_ticker_used": payload.get("_mfr_ticker_used", ticker),
        }
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO corpus_documents (
                        source, source_date, source_ref,
                        document_type, page_or_segment,
                        title, full_text, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source, COALESCE(source_ref, ''), source_date, COALESCE(page_or_segment, -1))
                    DO UPDATE SET
                        title     = EXCLUDED.title,
                        full_text = EXCLUDED.full_text,
                        metadata  = EXCLUDED.metadata
                    """,
                    (
                        "mfr_snapshot",
                        snapshot_date,
                        ticker,                  # source_ref = ticker symbol
                        "mfr_signal",
                        None,                    # page_or_segment unused
                        title,
                        body,
                        json.dumps(metadata, default=str),
                    ),
                )
            conn.commit()
        return True
    except Exception as e:
        log.warning("MFR -> corpus_documents write failed for %s: %s", ticker, e)
        return False


def fetch_and_save(ticker: str, snapshot_date: date | None = None) -> dict | None:
    """Fetch MFR for a ticker and persist to mfr_snapshots + corpus_documents.

    Returns the saved payload dict, or None if fetch failed.

    Dual-write semantics:
      - mfr_snapshots: typed columns, idempotent on (ticker, snapshot_date).
      - corpus_documents: searchable markdown body, upsert on (mfr_snapshot,
        ticker, snapshot_date, NULL) so FTS over the corpus surfaces MFR
        commentary alongside Hedgeye/SpotGamma/VolStudies docs.

    Either save can fail independently — a corpus write failure does not
    discard the typed mfr_snapshots row.
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
        # save_mfr_snapshot's 4th positional arg is `source_endpoint: str` —
        # a previous version of mfr_client passed `flat` here, which silently
        # typed-mismatched and caused psycopg2 'can't adapt type dict' once
        # MFR_API_TOKEN was finally set. The DB function extracts the typed
        # columns from `payload` directly, so `flat` isn't needed here at all.
        db_pg.save_mfr_snapshot(ticker, snapshot_date, payload)
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
        # Mirror to corpus_documents (best-effort, non-fatal).
        _save_to_corpus(ticker, snapshot_date, flat, payload)
        return payload
    except Exception as e:
        log.warning("MFR save failed for %s: %s", ticker, e)
        return None


def backfill_corpus_from_snapshots(*, since: date | None = None) -> dict:
    """Walk existing mfr_snapshots rows and write a corpus_documents row for
    each. Useful one-time after deploying the MFR-to-corpus hook so historical
    snapshots become searchable via FTS.

    Args:
        since: optional date floor; only rows with snapshot_date >= since
            are processed. None means all rows.

    Returns {"considered": N, "written": M, "failed": F}.
    """
    summary = {"considered": 0, "written": 0, "failed": 0}
    sql = "SELECT ticker, snapshot_date, full_payload FROM mfr_snapshots"
    params: list = []
    if since is not None:
        sql += " WHERE snapshot_date >= %s"
        params.append(since)
    sql += " ORDER BY snapshot_date DESC, ticker"

    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception as e:
        log.warning("backfill_corpus_from_snapshots query failed: %s", e)
        return summary

    for ticker, snap_date, full_payload in rows:
        summary["considered"] += 1
        # full_payload arrives as dict from psycopg2 JSONB
        payload = full_payload if isinstance(full_payload, dict) else {}
        flat = _flatten_for_save(payload)
        ok = _save_to_corpus(ticker, snap_date, flat, payload)
        if ok:
            summary["written"] += 1
        else:
            summary["failed"] += 1
    return summary


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


# ─────────────────────────── CLI smoke test ───────────────────────────

def _cli() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="mfr_client CLI — refresh tickers, backfill corpus from existing snapshots"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--refresh", action="store_true",
                   help="With --tickers, fetch+save (and mirror to corpus_documents)")
    g.add_argument("--refresh-watchlist", action="store_true",
                   help="Fetch the operator's MFR watchlist via GET /v2/asset "
                        "and refresh every ticker in it. The canonical fan-out "
                        "path — when the operator adds a ticker through the "
                        "MFR UI, this run picks it up automatically.")
    g.add_argument("--list-watchlist", action="store_true",
                   help="Print the operator's MFR watchlist (one ticker per "
                        "line) without fetching snapshots.")
    g.add_argument("--backfill-corpus", action="store_true",
                   help="Walk existing mfr_snapshots rows and write corpus_documents rows")
    g.add_argument("--latest", metavar="TICKER",
                   help="Print the latest mfr_snapshots row for TICKER")
    ap.add_argument("--tickers", nargs="+", help="Ticker list for --refresh")
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="Backfill: only process snapshots on/after this date")
    args = ap.parse_args()

    if args.refresh:
        if not args.tickers:
            print("--refresh requires --tickers TICKER1 TICKER2 ...")
            return
        summary = refresh_for_tickers(args.tickers)
        print(json.dumps(summary, indent=2))
        return

    if args.list_watchlist:
        tickers = list_watchlist()
        print("\n".join(tickers))
        return

    if args.refresh_watchlist:
        summary = refresh_watchlist()
        print(json.dumps(summary, indent=2))
        return

    if args.backfill_corpus:
        since_d = None
        if args.since:
            try:
                since_d = date.fromisoformat(args.since)
            except ValueError:
                print(f"--since must be YYYY-MM-DD, got {args.since!r}")
                return
        summary = backfill_corpus_from_snapshots(since=since_d)
        print(json.dumps(summary, indent=2))
        return

    if args.latest:
        try:
            import db_pg
            with db_pg.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT ticker, snapshot_date, price, range_low, range_high, "
                        "       trend_signal, momentum_signal, hurst, iv, rv, full_payload "
                        "  FROM mfr_snapshots WHERE ticker = %s "
                        " ORDER BY snapshot_date DESC LIMIT 1",
                        (args.latest.upper(),),
                    )
                    row = cur.fetchone()
                    if not row:
                        print(f"no rows for {args.latest!r}")
                        return
                    cols = [d[0] for d in cur.description]
                    record = dict(zip(cols, row))
                    # Print typed columns first, then truncated raw payload
                    typed = {k: v for k, v in record.items() if k != "full_payload"}
                    print(json.dumps(typed, indent=2, default=str))
                    print()
                    print("=== full_payload (truncated to 3000 chars) ===")
                    payload_str = json.dumps(record.get("full_payload") or {}, indent=2, default=str)
                    print(payload_str[:3000])
                    if len(payload_str) > 3000:
                        print(f"\n[... {len(payload_str) - 3000} more chars]")
        except Exception as e:
            print(f"latest({args.latest}) failed: {e}")
        return


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
