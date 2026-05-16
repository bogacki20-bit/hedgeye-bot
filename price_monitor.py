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

# Macro tickers (commodities, rates, FX, indices, crypto, vol) for which
# SpotGamma has NO direct equity-hub coverage. get_spotgamma_ctx's tier-2
# fallback was returning the latest founder's note (SPY/SPX-focused) for
# every macro ticker, so alerts for BRENT/UST10Y/GOLD/etc came out citing
# SPY's $750 call wall. Bypass the SG fetch entirely for these.
MACRO_NO_SG_TICKERS = {
    "BRENT", "WTIC", "NATGAS", "GOLD", "SILVER", "COPPER",
    "UST2Y", "UST10Y", "UST30Y",
    "USD", "EUR/USD", "GBP/USD", "CAD/USD", "USD/YEN",
    "BITCOIN", "VIX",
    "RUT", "COMPQ", "NIKK", "SSEC", "DAX", "FTSE",
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


def _framework_alignment(trend: str | None, zone: str) -> str:
    """Compute the alignment label for a (trend, zone) pair.

    Pre-fix the template hardcoded 'framework-aligned' on every bottom_third
    and top_third alert regardless of the actual trend; alerts on tickers
    with NEUTRAL or BEARISH trends still said 'framework-aligned' (a lie).

    Returns one of: 'aligned' / 'counter' / 'neutral' / 'stale'.
        bottom_third (buy zone): bullish=aligned, bearish=counter, neutral=neutral
        top_third    (trim zone): bearish=aligned, bullish=counter, neutral=neutral
        anything else: neutral
    """
    t = (trend or "").strip().lower()
    if t in ("bullish", "up"):
        if zone == "bottom_third": return "aligned"
        if zone == "top_third":    return "counter"
    elif t in ("bearish", "down"):
        if zone == "bottom_third": return "counter"
        if zone == "top_third":    return "aligned"
    elif t in ("neutral", ""):
        return "neutral"
    return "neutral"


def _framework_phrase(alignment: str) -> str:
    """Render alignment as the text fragment used inside the alert template."""
    return {
        "aligned":  "framework-aligned",
        "counter":  "framework-counter",
        "neutral":  "framework-neutral",
        "stale":    "framework-stale",
    }.get(alignment, "framework-neutral")


def _sg_levels_suffix(sg_ctx: dict | None, price: float | None) -> str:
    """Build the trailing ' | SG: call wall $X / put wall $Y / ...' suffix
    when SpotGamma context has populated levels. Empty string when no levels
    are available so the alert stays clean.

    Mirrors decision_engine._spotgamma_framing_line in spirit but tuned for
    the single-line alert format and the persisted ctx shape, where the
    levels can live either flat or nested under 'key_levels'.
    """
    if not sg_ctx or not isinstance(sg_ctx, dict):
        return ""
    levels = sg_ctx.get("key_levels") if isinstance(sg_ctx.get("key_levels"), dict) else sg_ctx
    cw = levels.get("call_wall")
    pw = levels.get("put_wall")
    kg = levels.get("key_gamma_strike")
    hw = levels.get("hedge_wall")

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    cw_f, pw_f, kg_f, hw_f = _f(cw), _f(pw), _f(kg), _f(hw)
    parts: list[str] = []
    if cw_f is not None: parts.append(f"call wall ${cw_f:g}")
    if pw_f is not None: parts.append(f"put wall ${pw_f:g}")
    if kg_f is not None: parts.append(f"key gamma ${kg_f:g}")
    if hw_f is not None: parts.append(f"hedge wall ${hw_f:g}")
    if not parts:
        return ""

    # Anchor sentence using call wall first, falling back to put wall.
    anchor_lv = cw_f if cw_f is not None else pw_f
    anchor_name = "call wall" if cw_f is not None else "put wall"
    try:
        p = float(price) if price is not None else None
    except (TypeError, ValueError):
        p = None
    anchor_phrase = ""
    if p is not None and anchor_lv:
        diff_pct = (p - anchor_lv) / anchor_lv * 100.0
        side = "above" if diff_pct > 0 else ("at" if abs(diff_pct) < 0.05 else "below")
        anchor_phrase = f" (price ${p:g} {side} {anchor_name} by {abs(diff_pct):.1f}%)"

    return " | SG: " + " / ".join(parts) + anchor_phrase


def _hurst_suffix(ticker: str) -> str:
    """Build the trailing ' | Hurst 0.62 (trending)' tag from the latest
    MFR snapshot. Empty string when MFR/Hurst is unavailable so the alert
    stays clean. Regime thresholds mirror decision_engine._hurst_regime_line
    (H >= 0.6 trending, H <= 0.4 mean_reverting, else random_walk).
    """
    try:
        from decision_engine import _get_mfr_latest
        mfr = _get_mfr_latest(ticker) or {}
        h = float(mfr.get("hurst")) if mfr.get("hurst") is not None else None
    except (TypeError, ValueError):
        return ""
    except Exception as e:
        log.warning(f"hurst suffix lookup failed for {ticker} (continuing): {e}")
        return ""
    if h is None:
        return ""
    if h >= 0.6:
        regime = "trending"
    elif h <= 0.4:
        regime = "mean_reverting"
    else:
        regime = "random_walk"
    return f" | Hurst {h:.2f} ({regime})"


def _quad_doctrine_suffix(ticker: str, side: str = "long") -> str:
    """Hedgeye Quad-doctrine tag for the alert: Quad alignment, the
    ticker's historical quarterly EV in the active Quad, and the
    position-cap headroom. Empty string on any lookup failure so the
    alert never breaks on doctrine/portfolio issues.
    """
    try:
        from tools.doctrine import (
            current_quarterly_quad, universe_for_quad, expected_return,
            asset_class_for, position_size_cap,
        )
    except Exception:
        return ""
    try:
        t = (ticker or "").upper()
        q = current_quarterly_quad()
        qn = q.split()[-1]
        longs = set(universe_for_quad(q, "longs"))
        shorts = set(universe_for_quad(q, "shorts"))
        if t in longs:
            favored, align = "long", ("aligned" if side == "long" else "counter")
        elif t in shorts:
            favored, align = "short", ("aligned" if side == "short" else "counter")
        else:
            favored, align = "neutral", "neutral"
        parts = [f" | Quad: {t} is Q{qn} favored {favored} ({align})"]

        ev = expected_return(t, q)
        if ev is not None:
            parts.append(f" | Q{qn} historical: {t} avg {ev:+.1f}% per quarter")

        ac = asset_class_for(t)
        if ac:
            try:
                from portfolio import account_value, position_summary
                acct_val = float(account_value(ticker=t) or 0.0)
                pos = position_summary(t) or {}
                cur = abs(float(pos.get("current_value") or 0.0))
            except Exception:
                acct_val, cur = 0.0, 0.0
            cap = position_size_cap(t, side, acct_val) if acct_val else None
            if cap:
                cur_pct = (cur / acct_val * 100.0) if acct_val else 0.0
                cap_pct = (cap / acct_val * 100.0) if acct_val else 0.0
                room = max(0.0, cap - cur)
                parts.append(
                    f" | Position: {ac} {cur_pct:.1f}% of {cap_pct:.0f}% cap"
                    f" — room for ${room:,.0f}")
        return "".join(parts)
    except Exception as e:
        log.warning(f"quad doctrine suffix failed for {ticker} (continuing): {e}")
        return ""


def _cross_source_suffix(ticker: str, sg_ctx: dict | None) -> str:
    """Build the trailing cross-source alignment tag from RR / MFR / ETF Pro
    / SG. Empty string unless there is a strong signal (high-conviction
    cluster, or divergence worth a caution). Reuses decision_engine's
    cross-source math so the long-form prompt and this tag never drift.
    """
    try:
        from decision_engine import (
            _get_risk_range, _get_mfr_latest, _get_etf_pro_range,
            _cross_source_eval, _xs_fmt,
        )
        sg = sg_ctx or {}
        if isinstance(sg.get("key_levels"), dict):
            sg = sg["key_levels"]
        dctx = {
            "rr":  _get_risk_range(ticker) or {},
            "mfr": _get_mfr_latest(ticker) or {},
            "etf_pro": _get_etf_pro_range(ticker) or {},
            "sg":  sg,
        }
        ev = _cross_source_eval(dctx)
    except Exception as e:
        log.warning(f"cross-source suffix lookup failed for {ticker} (continuing): {e}")
        return ""
    if not ev:
        return ""
    strong = [c for c in ev["clusters"] if c["n_sources"] >= 3]
    if ev["high_conviction"] or strong:
        if strong:
            best = max(strong, key=lambda c: c["n_sources"])
            return (f" | Cross-source: {best['n_sources']} sources align at "
                    f"level cluster {_xs_fmt(best['center'])} (high-conviction zone)")
        return (f" | Cross-source: {ev['n_sources']} sources align "
                f"(lows within {ev['low_spread']:.1f}%, highs within "
                f"{ev['high_spread']:.1f}%) (high-conviction zone)")
    if ev["divergence"]:
        return (f" | Cross-source: sources diverge (low spread "
                f"{ev['low_spread']:.1f}%, high spread {ev['high_spread']:.1f}%) — caution")
    return ""


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
        text                 — human-readable suggestion (e.g. "ADD ~$250 OIH (50 bps)")
        suggested_action     — verb: BUY / ADD / TRIM / SELL / WATCH
        suggested_dollars    — float (None if no specific amount)
        suggested_bps        — int (50 or 100; None for trim/watch)
        framework_alignment  — "aligned" / "counter" / "neutral" / "stale"
        hedgeye_context      — dict snapshot (today's quad, vix bucket, etc)
        spotgamma_context    — dict snapshot (call wall / put wall / hedge wall if known)

    Logic mirrors Hedgeye U Ch2 framework: "top of range you sell, bottom of
    range you buy" (Risk Range Signal Deep Dive). Sizing uses bps per
    framework_quotes_compiled.md Ch3 Lessons 2 + 4 (100 bps starter, 50 bps
    adds, $1K real-world ceiling):
        bottom_third  -> ADD  at ADD_BPS_LOW (50 bps of account, $1K cap)
        below_range   -> BUY  at STARTER_BPS (100 bps starter, broken range)
        top_third     -> TRIM (50% of position)
        above_range   -> WATCH (range break above — let it ride or trim?)

    The caller passes Style B parameters; this is just the alert-time hint.
    Final position-sizing math lives in recommender.py.
    """
    icon, label = ZONE_LABELS.get(zone, ("", zone))

    # Hedgeye / SpotGamma context — pulled from recent corpus_documents by
    # monitor_context. Both lookups are non-fatal (return {} on any error) and
    # TTL-cached so a fanout cycle doesn't hammer the DB. Resulting dicts get
    # persisted on alerts_fired (JSONB) for ML training context.
    # Skip SG entirely for macro tickers — SpotGamma has no per-ticker
    # coverage for commodities / rates / FX / VIX / global indices, and
    # get_spotgamma_ctx's tier-2 fallback returns the latest SPX-focused
    # founder's note for ANY non-tier-1 ticker. That's how BRENT and UST10Y
    # alerts ended up citing $750/$739 walls (the SPY levels).
    _macro_skip_sg = ticker.upper() in MACRO_NO_SG_TICKERS

    try:
        from monitor_context import get_hedgeye_ctx, get_spotgamma_ctx
        hedgeye_ctx = get_hedgeye_ctx() or {}
        spotgamma_ctx = ({} if _macro_skip_sg
                         else (get_spotgamma_ctx(ticker) or {}))
    except Exception as e:
        log.warning(f"monitor_context lookup failed (continuing with empty ctx): {e}")
        hedgeye_ctx = {}
        spotgamma_ctx = {}

    # Suggested $ size: Style B (bps × account value, clamped at $1K per-fill
    # ceiling). Single source of truth is recommender.size_for(conviction, account).
    # The decision_engine (slice 0b) will eventually replace these inline
    # calculations with a Claude API call synthesizing multi-source context.
    try:
        from recommender import size_for, STYLE_B
        from portfolio import hedgeye_target_account
        _acct          = hedgeye_target_account(direction="Long")
        _starter_usd, _starter_dbg = size_for("Best Idea", _acct)
        _add_usd,     _add_dbg     = size_for("Adding",    _acct)
        # size_for returns None when account_value is zero/missing — keep the
        # placeholder so alert text still has a number. Same fallback path the
        # legacy size_from_bps branch used.
        if not _starter_usd:
            _starter_usd = 500.0
        if not _add_usd:
            _add_usd = 500.0
        STARTER_BPS = STYLE_B["starter_bps"]
        ADD_BPS_LOW = STYLE_B["add_bps"]
    except Exception as e:
        log.warning(f"size_for lookup failed; using $500 placeholder: {e}")
        _starter_usd, _add_usd = 500.0, 500.0
        STARTER_BPS, ADD_BPS_LOW = 100, 50

    # Build SG suffix once and append to every zone's text (when SG has
    # populated levels). Keeps the alert visibly anchored to dealer levels —
    # the path bypasses decision_engine entirely so 8b1cab2's flagger never
    # fires on these boundary alerts. Cited directly in the template instead.
    sg_suffix = _sg_levels_suffix(spotgamma_ctx, price)
    # Hurst regime tag appended after the SG suffix (i.e. after the trend tag)
    # so the alert carries MFR's trending / mean-reverting read at a glance.
    hurst_suffix = _hurst_suffix(ticker)
    # Cross-source range alignment tag (RR/MFR/ETF Pro/SG) — only renders on a
    # strong signal (high-conviction cluster or divergence caution).
    xs_suffix = _cross_source_suffix(ticker, spotgamma_ctx)
    # Hedgeye Quad-doctrine tag: alignment + historical EV + cap headroom.
    quad_suffix = _quad_doctrine_suffix(ticker, side="long")

    if zone == "bottom_third":
        _align = _framework_alignment(trend, "bottom_third")
        return {
            "text": (f"ADD ~${_add_usd:.0f} {ticker} at {price:.2f} ({ADD_BPS_LOW} bps, "
                     f"bottom third of range, {_framework_phrase(_align)}).{sg_suffix}{hurst_suffix}{xs_suffix}{quad_suffix}"),
            "suggested_action": "ADD",
            "suggested_dollars": _add_usd,
            "suggested_bps": ADD_BPS_LOW,
            "framework_alignment": _align,
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    if zone == "below_range":
        return {
            "text": (f"BUY ~${_starter_usd:.0f} {ticker} at {price:.2f} ({STARTER_BPS} bps starter, "
                     f"broken below range, contrarian add).{sg_suffix}{hurst_suffix}{xs_suffix}{quad_suffix}"),
            "suggested_action": "BUY",
            "suggested_dollars": _starter_usd,
            "suggested_bps": STARTER_BPS,
            "framework_alignment": "neutral",
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    if zone == "top_third":
        _align = _framework_alignment(trend, "top_third")
        return {
            "text": (f"TRIM 50% {ticker} at {price:.2f} "
                     f"(top third of range, fade strength, {_framework_phrase(_align)}).{sg_suffix}{hurst_suffix}{xs_suffix}{quad_suffix}"),
            "suggested_action": "TRIM",
            "suggested_dollars": None,
            "suggested_bps": None,
            "framework_alignment": _align,
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    if zone == "above_range":
        return {
            "text": (f"WATCH {ticker} at {price:.2f} — broke above range. "
                     f"Trim or let it run?{sg_suffix}{hurst_suffix}{xs_suffix}{quad_suffix}"),
            "suggested_action": "WATCH",
            "suggested_dollars": None,
            "suggested_bps": None,
            "framework_alignment": "neutral",
            "hedgeye_context": hedgeye_ctx,
            "spotgamma_context": spotgamma_ctx,
        }
    return {
        "text": f"{ticker} at {price:.2f} ({zone}) — no specific recommendation.",
        "suggested_action": None,
        "suggested_dollars": None,
        "suggested_bps": None,
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
        body += f"\n\nReply A{alert_id} BUY/PASS/LATER (or A{alert_id} BUY $<amount>)."

    return title, body


# ─────────────────────────── Cycle / loop ───────────────────────────

def get_alert_ticker_universe() -> set:
    """Return the set of tickers price_monitor should fire alerts for.

    Prefers the SQL VIEW `monitored_tickers` (combines hedgeye_ticker_inventory
    is_active rows + recent Risk Range mentions). Falls back to the hardcoded
    ALERT_TICKERS set if the view is unavailable (db down, migration 004 not
    yet applied, etc.) so we never silently mute the alert system.
    """
    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ticker FROM monitored_tickers")
                rows = cur.fetchall()
                if rows:
                    dynamic = {r[0] for r in rows if r[0]}
                    if dynamic:
                        return dynamic | ALERT_TICKERS  # union: never narrower than the static list
    except Exception as e:
        log.debug(f"monitored_tickers view query failed, falling back to ALERT_TICKERS: {e}")
    return set(ALERT_TICKERS)


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
    alert_universe = get_alert_ticker_universe()
    log.info(f"Monitor cycle: {len(alert_universe)} tickers in alert universe "
             f"(static={len(ALERT_TICKERS)}, dynamic VIEW union)")

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

        if ticker not in alert_universe:
            # Mapped but not in alert universe (e.g. FX, yields, or ticker not
            # currently signaled-on by Hedgeye). Skip silently.
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
