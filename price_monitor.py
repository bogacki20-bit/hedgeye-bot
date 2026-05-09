"""
Live price monitor — polls yfinance, compares to Hedgeye Risk Range levels,
fires Telegram alerts when price enters scale-in / trim zones or breaks
out of the range entirely.

Reads `hedgeye_risk_ranges` (populated by parser_risk_range.py from emails),
gets the most recent range per ticker, fetches live price via yfinance,
computes range zone (bottom/middle/top third) and boundary breaches, fires
deduped alerts via Telegram + `alerts_fired` table.

Architecture:
  parser_risk_range.py → hedgeye_risk_ranges (typed levels)
  price_monitor.py     → reads levels + fetches yfinance prices + alerts
  notifier.send_telegram → user's phone

Operational:
  Run via main.py as a daemon thread (gated by MONITOR_ENABLED env var,
  defaults true). Standalone for testing:
      python price_monitor.py            (one cycle against live DB)
      python price_monitor.py --dry-run  (one cycle, no Telegram, no DB writes)
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ─────────────────────────── Config ───────────────────────────

MONITOR_INTERVAL_OPEN  = int(os.getenv("MONITOR_INTERVAL_OPEN",  "300"))   # 5 min during market hours
MONITOR_INTERVAL_CLOSED = int(os.getenv("MONITOR_INTERVAL_CLOSED", "1800"))  # 30 min when closed
MAX_RISK_RANGE_AGE_DAYS = int(os.getenv("MAX_RISK_RANGE_AGE_DAYS", "5"))   # ignore ranges older than this

ET = ZoneInfo("America/New_York")
MARKET_OPEN  = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)


# ─────────────────────────── Hedgeye → yfinance ticker mapping ───────────────────────────

# Maps Hedgeye Risk Range ticker labels to yfinance symbols.
# Tickers not in this map are skipped with a warning each cycle.
# Add entries as new instruments come into the Risk Range universe.
HEDGEYE_TO_YFINANCE = {
    # US equity ETFs (identity)
    "HYG":     "HYG",
    "LQD":     "LQD",
    "XLK":     "XLK",
    "XOP":     "XOP",
    "XTL":     "XTL",
    "XLI":     "XLI",
    "OIH":     "OIH",
    "KRE":     "KRE",
    "AAAU":    "AAAU",
    "ALLW":    "ALLW",

    # US single stocks (identity)
    "MSFT":    "MSFT",
    "AAPL":    "AAPL",
    "AMZN":    "AMZN",
    "META":    "META",
    "GOOGL":   "GOOGL",
    "NFLX":    "NFLX",
    "TSLA":    "TSLA",
    "NVDA":    "NVDA",
    "ORCL":    "ORCL",

    # Major US indices (Yahoo uses ^ prefix)
    "SPX":     "^GSPC",
    "COMPQ":   "^IXIC",
    "RUT":     "^RUT",
    "VIX":     "^VIX",

    # International indices
    "DAX":     "^GDAXI",
    "NIKK":    "^N225",
    "SSEC":    "000001.SS",   # Shanghai Composite

    # Commodities (futures)
    "WTIC":    "CL=F",        # WTI crude oil
    "BRENT":   "BZ=F",        # Brent crude
    "NATGAS":  "NG=F",        # Natural gas
    "GOLD":    "GC=F",        # Gold
    "SILVER":  "SI=F",        # Silver
    "COPPER":  "HG=F",        # Copper

    # Crypto
    "BITCOIN": "BTC-USD",

    # FX (informational only — alerts disabled by default)
    "EUR/USD": "EURUSD=X",
    "USD/YEN": "JPY=X",
    "GBP/USD": "GBPUSD=X",
    "CAD/USD": "CAD=X",
    "USD":     "DX-Y.NYB",    # US Dollar Index

    # Treasury yields (informational — note these are yields, not prices)
    "UST30Y":  "^TYX",
    "UST10Y":  "^TNX",
    "UST2Y":   "^IRX",        # technically 13wk T-bill; closest free yfinance proxy
}

# Tickers to alert on (subset of the map). Everything else is logged but no Telegram.
# Treasury yields stay informational-only for now (yield-vs-bond-price inversion
# makes "trim/scale-in" semantics confusing without an explicit ETF mapping).
# FX pairs ARE alerted on — Kristian trades currencies via equity wrappers
# (FXE/FXY/FXB/FXC/UUP) and crypto platforms; he translates the rate alert
# to his actual instrument.
ALERT_TICKERS = {
    # Equity ETFs
    "HYG", "LQD", "XLK", "XOP", "XTL", "XLI", "OIH", "KRE", "AAAU", "ALLW",
    # US single stocks
    "MSFT", "AAPL", "AMZN", "META", "GOOGL", "NFLX", "TSLA", "NVDA", "ORCL",
    # Indices
    "SPX", "COMPQ", "RUT", "VIX",
    # Commodities
    "WTIC", "BRENT", "NATGAS", "GOLD", "SILVER", "COPPER",
    # Crypto
    "BITCOIN",
    # FX pairs (rate-level alerts; user translates to equity wrappers / crypto pair)
    "EUR/USD", "USD/YEN", "GBP/USD", "CAD/USD", "USD",
}

# Equity-wrapper hints for FX alerts. These get appended to Telegram messages
# so the alert reminds Kristian which ETF (or proxy) tracks the signal.
FX_EQUITY_WRAPPER = {
    "EUR/USD": "FXE",
    "USD/YEN": "FXY (inverse) or YCS",
    "GBP/USD": "FXB",
    "CAD/USD": "FXC",
    "USD":     "UUP",
}


# ─────────────────────────── Time / zones ───────────────────────────

def is_market_hours(now: datetime | None = None) -> bool:
    """Returns True during US equity regular session (9:30-16:00 ET, M-F)."""
    n = now or datetime.now(ET)
    if n.weekday() >= 5:
        return False
    return MARKET_OPEN <= n.time() <= MARKET_CLOSE


def is_extended_hours(now: datetime | None = None) -> bool:
    """4:00 AM - 8:00 PM ET, M-F. Useful for crypto + futures contexts."""
    n = now or datetime.now(ET)
    if n.weekday() >= 5:
        return n.weekday() == 5 and n.time() < dtime(2, 0)  # rough cutoff
    return dtime(4, 0) <= n.time() <= dtime(20, 0)


# ─────────────────────────── Range-zone math ───────────────────────────

def compute_zone(price: float, low: float, high: float) -> str:
    """
    Returns 'bottom_third', 'middle_third', 'top_third', 'below_range', or 'above_range'.

    Range thirds match Kristian's Hedgeye-style sizing rule:
      bottom_third = scale-in zone
      middle_third = hold
      top_third    = trim
    Below/above range = boundary breach (more urgent).
    """
    if low is None or high is None or low >= high:
        return "unknown"
    if price < low:
        return "below_range"
    if price > high:
        return "above_range"
    span = high - low
    rel = (price - low) / span
    if rel < 0.333:
        return "bottom_third"
    if rel > 0.667:
        return "top_third"
    return "middle_third"


def boundaries_for_alert(zone: str) -> str | None:
    """
    Given a zone, returns the alert boundary label (matches alerts_fired.boundary
    column values) or None if no alert.

    We alert on:
      - boundary breaches (below_range / above_range)  → range_low / range_high
      - zone entry into actionable thirds                → top_third / bottom_third
    Middle third = no alert (would be noise).
    """
    return {
        "below_range":  "range_low",
        "above_range":  "range_high",
        "bottom_third": "bottom_third",
        "top_third":    "top_third",
    }.get(zone)


# ─────────────────────────── yfinance price fetch ───────────────────────────

def fetch_prices(yf_symbols: list[str]) -> dict[str, float]:
    """
    Fetch latest prices for a list of yfinance symbols. Returns
    {symbol: last_price} for symbols that returned data; missing symbols
    are silently dropped (logged at WARNING).
    """
    if not yf_symbols:
        return {}
    import yfinance as yf  # lazy — only loaded when needed
    prices: dict[str, float] = {}
    try:
        data = yf.download(
            yf_symbols,
            period="2d",
            interval="1m",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
    except Exception as e:
        log.error(f"yfinance batch download failed: {e}")
        return {}

    for sym in yf_symbols:
        try:
            if len(yf_symbols) == 1:
                series = data["Close"]
            else:
                series = data[sym]["Close"]
            last = series.dropna().iloc[-1]
            prices[sym] = float(last)
        except (KeyError, IndexError, ValueError, TypeError):
            log.warning(f"  No price data for {sym}")
    return prices


# ─────────────────────────── Telegram alert formatting ───────────────────────────

ZONE_LABELS = {
    "below_range":  ("⚠️", "RANGE BREAK (BELOW)"),
    "above_range":  ("⚠️", "RANGE BREAK (ABOVE)"),
    "bottom_third": ("🟢", "BUY ZONE — bottom third"),
    "top_third":    ("🔴", "TRIM ZONE — top third"),
}


def compose_recommendation(
    ticker: str,
    zone: str,
    price: float,
    low: float,
    high: float,
    trend: str | None,
) -> dict:
    """Translate a zone + Risk Range edge into a recommendation dict.

    Returns keys (all optional / nullable):
        text                 — human-readable suggestion ("ADD ~$1500 in OIH")
        suggested_action     — verb: BUY / ADD / TRIM / SELL / WATCH
        suggested_dollars    — float (None if no specific amount)
        framework_alignment  — "aligned" / "counter" / "neutral" / "stale"
        hedgeye_context      — dict snapshot (today's quad, vix bucket, etc)
        spotgamma_context    — dict snapshot (call wall / put wall / hedge wall if known)

    Logic mirrors Hedgeye U Ch2 framework: "top of range you sell, bottom of
    range you buy" (Risk Range Signal Deep Dive). Style B sizing per
    recommender.SIZING:
        bottom_third  -> ADD  ~$1500 (3% of $50K, capped)
        below_range   -> ADD  ~$1500 (range break, deeper opportunity)
        top_third     -> TRIM (50% of position)
        above_range   -> WATCH (range break above — let it ride or trim?)

    The caller passes Style B parameters; this is just the alert-time hint.
    Final position-sizing math lives in recommender.py.
    """
    icon, label = ZONE_LABELS.get(zone, ("", zone))

    # Lazy import — Hedgeye/SpotGamma context functions are aspirational and
    # come from corpus_documents queries we wire up later.
    hedgeye_ctx = {"current_quad": "Quad 2", "vix_bucket": "investable"}
    spotgamma_ctx: dict = {}

    if zone == "bottom_third":
        return {
            "text": f"ADD ~$1500 {ticker} at {price:.2f} (bottom third of range, framework-aligned).",
            "suggested_action": "ADD",
            "suggested_dollars": 1500.0,
            "framework_alignment": "aligned" if trend in ("bullish", "up") else "neutral",
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    if zone == "below_range":
        return {
            "text": f"BUY ~$1500 {ticker} at {price:.2f} (broken below range, contrarian add).",
            "suggested_action": "BUY",
            "suggested_dollars": 1500.0,
            "framework_alignment": "neutral",
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    if zone == "top_third":
        return {
            "text": f"TRIM 50% {ticker} at {price:.2f} (top third of range, fade strength).",
            "suggested_action": "TRIM",
            "suggested_dollars": None,
            "framework_alignment": "aligned" if trend in ("bullish", "up") else "neutral",
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    if zone == "above_range":
        return {
            "text": f"WATCH {ticker} at {price:.2f} — broke above range. Trim or let it run?",
            "suggested_action": "WATCH",
            "suggested_dollars": None,
            "framework_alignment": "neutral",
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    return {
        "text": f"{ticker} at {price:.2f} ({zone}) — no specific recommendation.",
        "suggested_action": None,
        "suggested_dollars": None,
        "framework_alignment": "neutral",
        "hedgeye_context": hedgeye_ctx,
        "spotgamma_context": spotgamma_ctx,
    }


def format_alert_message(ticker: str, price: float, low: float, high: float,
                         prev_close: float | None, trend: str | None,
                         zone: str, signal_date,
                         alert_id: int | None = None,
                         recommendation_text: str | None = None) -> tuple[str, str]:
    """Returns (title, body) ready for send_telegram.

    When `alert_id` is provided, the body includes "Reply A{id} BUY/PASS/etc"
    so the user can reply directly from Telegram and the parser links the
    decision back to this alert in user_actions.
    """
    icon, label = ZONE_LABELS.get(zone, ("ℹ️", zone))
    id_suffix = f" [A{alert_id}]" if alert_id else ""
    title = f"{icon} {ticker}{id_suffix} — {label}"

    pct_in_range = ""
    if low and high and high > low:
        pct = (price - low) / (high - low) * 100
        pct_in_range = f"{pct:.0f}% of range\n"

    prev = f"prev close {prev_close:.2f}" if prev_close else "prev close n/a"
    trend_line = f"trend {trend}" if trend else "trend n/a"

    body = (
        f"price `{price:.2f}`\n"
        f"buy `{low:.2f}` — sell `{high:.2f}`\n"
        f"{pct_in_range}"
        f"{prev}, {trend_line}\n"
        f"signal {signal_date}"
    )

    wrapper = FX_EQUITY_WRAPPER.get(ticker)
    if wrapper:
        body += f"\nequity wrapper: {wrapper}"

    if recommendation_text:
        body += f"\n\n{recommendation_text}"
    if alert_id:
        body += f"\n\nReply A{alert_id} BUY/PASS/LATER (or A{alert_id} BUY $1500)."

    return title, body


# ─────────────────────────── Cycle / loop ───────────────────────────

def run_monitor_cycle(dry_run: bool = False) -> dict:
    """
    Process one monitor cycle. Returns summary dict.

    Flow:
      1. Pull active Risk Ranges from db_pg.
      2. Filter to mappable + recent (within MAX_RISK_RANGE_AGE_DAYS).
      3. Fetch prices from yfinance.
      4. For each, compute zone, dedup against alerts_fired, fire Telegram.
    """
    summary = {
        "tickers_examined":    0,
        "tickers_no_mapping":  0,
        "tickers_stale_range": 0,
        "tickers_no_price":    0,
        "alerts_fired_new":    0,
        "alerts_deduped":      0,
        "alerts_skipped_zone": 0,
    }

    import db_pg  # lazy

    rows = db_pg.get_active_risk_ranges()
    summary["tickers_examined"] = len(rows)
    if not rows:
        log.info("No active Risk Ranges in DB — nothing to monitor yet.")
        return summary

    today = datetime.now(ET).date()

    # Filter + map tickers, build symbol list for batch fetch
    candidates = []
    yf_to_hedgeye: dict[str, str] = {}
    for row in rows:
        ticker = row["ticker"]
        signal_date = row["signal_date"]

        if (today - signal_date).days > MAX_RISK_RANGE_AGE_DAYS:
            summary["tickers_stale_range"] += 1
            continue

        yf_sym = HEDGEYE_TO_YFINANCE.get(ticker)
        if not yf_sym:
            summary["tickers_no_mapping"] += 1
            log.debug(f"  no yfinance mapping for {ticker}")
            continue

        if ticker not in ALERT_TICKERS:
            # Mapped but not in alert universe (e.g. FX, yields). Skip silently.
            continue

        candidates.append(row)
        yf_to_hedgeye[yf_sym] = ticker

    if not candidates:
        log.info("No ALERT_TICKERS with recent ranges to monitor.")
        return summary

    log.info(f"Monitor cycle: fetching prices for {len(candidates)} tickers...")
    prices = fetch_prices(list(yf_to_hedgeye.keys()))

    for row in candidates:
        ticker      = row["ticker"]
        signal_date = row["signal_date"]
        low         = float(row["buy_trade"])  if row["buy_trade"]  is not None else None
        high        = float(row["sell_trade"]) if row["sell_trade"] is not None else None
        prev_close  = float(row["prev_close"]) if row["prev_close"] is not None else None
        trend       = row["trend"]

        yf_sym = HEDGEYE_TO_YFINANCE[ticker]
        price = prices.get(yf_sym)
        if price is None:
            summary["tickers_no_price"] += 1
            continue

        zone = compute_zone(price, low, high)
        boundary = boundaries_for_alert(zone)
        if not boundary:
            summary["alerts_skipped_zone"] += 1
            log.debug(f"  {ticker}: zone={zone} (no alert)")
            continue

        # Dedup
        if db_pg.has_alert_fired(ticker, boundary, signal_date):
            summary["alerts_deduped"] += 1
            log.debug(f"  {ticker}: already alerted on {boundary} for {signal_date}")
            continue

        # Build recommendation first so we can record it with the alert.
        rec = compose_recommendation(ticker, zone, price, low, high, trend)
        range_at_fire = {"low": low, "high": high, "trend": trend}

        if dry_run:
            # In dry-run we still build a synthetic alert id for message formatting.
            title, body = format_alert_message(
                ticker, price, low, high, prev_close, trend, zone, signal_date,
                alert_id=0, recommendation_text=rec.get("text"),
            )
            log.info(f"DRY-RUN ALERT — {title}\n{body}")
        else:
            # Record the alert FIRST so we have an id to put in the Telegram message.
            # Use a temporary placeholder notification_id; we'll learn whether
            # send succeeded in a follow-up step.
            alert_id = db_pg.record_alert(
                ticker=ticker,
                boundary=boundary,
                signal_date=signal_date,
                range_zone=zone,
                price_at_fire=price,
                range_at_fire=range_at_fire,
                notification_id="telegram_pending",
                recommendation_text=rec.get("text"),
                suggested_action=rec.get("suggested_action"),
                suggested_dollars=rec.get("suggested_dollars"),
                framework_alignment=rec.get("framework_alignment"),
                hedgeye_context=rec.get("hedgeye_context"),
                spotgamma_context=rec.get("spotgamma_context"),
            )
            if alert_id is None:
                # ON CONFLICT collapsed - already alerted (rare race after has_alert_fired check)
                log.debug(f"  {ticker}: alert id collision (already fired)")
                continue

            title, body = format_alert_message(
                ticker, price, low, high, prev_close, trend, zone, signal_date,
                alert_id=alert_id, recommendation_text=rec.get("text"),
            )
            from notifier import send_telegram
            sent = send_telegram(title, body)

            # Update notification_id on the alert row to reflect whether send worked.
            try:
                with db_pg.get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE alerts_fired SET notification_id=%s WHERE id=%s",
                            ("telegram_ok" if sent else "telegram_failed", alert_id),
                        )
                    conn.commit()
            except Exception as e:
                log.warning(f"  failed to update notification_id for alert {alert_id}: {e}")

            log.info(f"  ALERT fired: A{alert_id} {ticker} {zone} at {price}")

        summary["alerts_fired_new"] += 1

    return summary


def run_monitor_loop():
    """Forever loop. Polls more frequently during market hours, slower outside."""
    log.info(
        f"Starting price monitor loop — interval open={MONITOR_INTERVAL_OPEN}s, "
        f"closed={MONITOR_INTERVAL_CLOSED}s, max range age={MAX_RISK_RANGE_AGE_DAYS}d"
    )
    while True:
        try:
            in_session = is_market_hours()
            if in_session:
                summary = run_monitor_cycle()
                if summary.get("alerts_fired_new", 0) > 0 or summary["tickers_examined"] > 0:
                    log.info(f"Monitor cycle done: {summary}")
            else:
                log.debug("Outside market hours — skipping cycle.")
        except Exception as e:
            log.error(f"Monitor cycle error: {e}", exc_info=True)

        sleep_for = MONITOR_INTERVAL_OPEN if is_market_hours() else MONITOR_INTERVAL_CLOSED
        time.sleep(sleep_for)


# ─────────────────────────── Standalone test ───────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Run one cycle, log alerts but don't send Telegram or write DB")
    args = parser.parse_args()

    print(f"Running one monitor cycle (dry_run={args.dry_run})...")
    print(f"is_market_hours = {is_market_hours()}")
    summary = run_monitor_cycle(dry_run=args.dry_run)
    print(f"Summary: {summary}")
