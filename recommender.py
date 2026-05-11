"""
Trade Recommender
Takes a classified Hedgeye signal (from classifier.py) and the current
portfolio state (from portfolio.py), decides on a sized recommendation,
logs it to SQLite, and returns a Pushover-ready summary.

SIZING FRAMEWORK (per Kristian + Hedgeye U Ch3 + framework_quotes_compiled.md)
-----------------------------------------------------------------------------
The trade size is computed from a basis-points-of-account-value (bps) starter
or add, then clamped to a hard real-world per-fill dollar ceiling. Style B.
"""

import logging
from datetime import datetime

from database import get_conn
from portfolio import (
    ACCOUNTS,
    INDIVIDUAL_ACCOUNT,
    MARGIN_BUFFER_USD,
    account_value,
    can_trade,
    hedgeye_target_account,
    position_summary,
)

log = logging.getLogger(__name__)

STARTER_BPS         = 100
ADD_BPS_LOW         = 50
ADD_BPS_HIGH        = 100
PER_FILL_CEILING_USD = 1000
INCREMENTAL_FILL_CAP_PCT = 0.33

ALLOWED_BPS = (ADD_BPS_LOW, ADD_BPS_HIGH, STARTER_BPS)

STYLE_B = {
    "starter_bps":          STARTER_BPS,
    "add_bps":              ADD_BPS_LOW,
    "per_fill_ceiling_usd": PER_FILL_CEILING_USD,
}

_STYLE_B_BPS_BY_CONVICTION = {
    "Best Idea": STYLE_B["starter_bps"],
    "Adding":    STYLE_B["add_bps"],
}

SIZING = {
    "Best Idea": {"bps_default": STARTER_BPS,  "is_close": False, "trim": None},
    "Adding":    {"bps_default": ADD_BPS_LOW,  "is_close": False, "trim": None},
    "Reducing":  {"bps_default": None,         "is_close": False, "trim": 0.50},
    "Remove":    {"bps_default": None,         "is_close": True,  "trim": 1.00},
    "Monitor":   {"bps_default": None,         "is_close": False, "trim": None},
}


def size_from_bps(bps, acct_value):
    if bps not in ALLOWED_BPS:
        raise ValueError(f"bps={bps} not in ALLOWED_BPS={ALLOWED_BPS}")
    raw = acct_value * (bps / 10_000.0)
    return round(min(raw, PER_FILL_CEILING_USD), 2)


def size_for(conviction, account):
    """Style B sizing -- conviction + account -> (dollars, debug)."""
    acct_value = float(account_value(account) or 0.0)
    bps = _STYLE_B_BPS_BY_CONVICTION.get(conviction)
    if bps is None:
        return None, {"bps": None, "acct_value": acct_value, "raw": None, "clamped_by": None}
    raw = acct_value * (bps / 10_000.0)
    ceiling = STYLE_B["per_fill_ceiling_usd"]
    if raw > ceiling:
        dollars = float(ceiling)
        clamped_by = "ceiling"
    else:
        dollars = round(raw, 2)
        clamped_by = None
    return dollars, {"bps": bps, "acct_value": acct_value, "raw": round(raw, 2), "clamped_by": clamped_by}


def recommend_from_signal(item):
    if item.get("classified_type") != "trade_signal":
        return None
    ticker     = (item.get("ticker") or "").upper()
    direction  = item.get("direction", "Long")
    conviction = item.get("conviction", "")
    if not ticker:
        return None

    account     = hedgeye_target_account(direction)
    cfg         = ACCOUNTS[account]
    current     = position_summary(ticker, account=account)
    held_shares = current["shares"]      if current else 0.0
    last_price  = current["last_price"]  if current else None
    if not last_price:
        any_pos = position_summary(ticker)
        if any_pos:
            last_price = any_pos["last_price"]
    if not last_price:
        last_price = item.get("last_price")

    rec = {
        "signal_item_id":      item.get("id"),
        "ticker":               ticker,
        "direction":            direction,
        "conviction":           conviction,
        "account":              account,
        "action":               "SKIP",
        "recommended_dollars":  None,
        "recommended_shares":   None,
        "reference_price":      last_price,
        "current_shares":       held_shares,
        "reasoning":            "",
    }

    allowed, reason = can_trade(account, direction, instrument="ETF")
    if not allowed:
        rec["reasoning"] = f"Blocked: {reason}"
        return _save(rec, current)

    if conviction == "Monitor" or conviction not in SIZING:
        rec["reasoning"] = f"Monitor only -- no trade for conviction={conviction!r}."
        return _save(rec, current)

    if conviction in ("Reducing", "Remove"):
        if held_shares <= 0:
            rec["reasoning"] = f"{conviction} signal but no current position in {ticker}."
            return _save(rec, current)
        trim_pct = SIZING[conviction]["trim"]
        shares   = round(held_shares * trim_pct, 3)
        rec["action"]              = "COVER" if direction.lower() == "short" else "SELL"
        rec["recommended_shares"]  = shares
        rec["recommended_dollars"] = round(shares * (last_price or 0), 2)
        rec["reasoning"] = (
            f"{conviction} {ticker}: trim {int(trim_pct * 100)}% of "
            f"{held_shares} shares ({cfg['name']})."
        )
        return _save(rec, current)

    raw_dollars, dbg = size_for(conviction, account)
    dollars          = _respect_buffer(account, raw_dollars, direction)
    shares           = round(dollars / last_price, 3) if last_price and last_price > 0 else None

    rec["action"]              = "SHORT" if direction.lower() == "short" else "BUY"
    rec["recommended_dollars"] = dollars
    rec["recommended_shares"]  = shares
    rec["reasoning"] = _explain_size(conviction, ticker, dollars, raw_dollars, dbg, account, held_shares)
    return _save(rec, current)


def _respect_buffer(account, dollars, direction):
    cfg = ACCOUNTS.get(account, {})
    if not cfg.get("margin_buffer"):
        return dollars
    headroom = max(account_value(account) - cfg["margin_buffer"], 0)
    return round(min(dollars, headroom), 2)


def _explain_size(conviction, ticker, dollars, raw_after_ceiling, dbg, account, held):
    name       = ACCOUNTS[account]["name"]
    bps        = dbg["bps"]
    acct_value = dbg["acct_value"]
    parts = [
        f"{conviction} {ticker}: ${dollars:,.0f} in {name} "
        f"({bps} bps of ${acct_value:,.0f}, per-fill cap ${PER_FILL_CEILING_USD:,.0f})."
    ]
    if dbg.get("clamped_by") == "ceiling":
        parts.append(
            f"Raw {bps} bps x ${acct_value:,.0f} = ${dbg['raw']:,.0f} "
            f"clamped to ${PER_FILL_CEILING_USD:,.0f} ceiling."
        )
    if dollars < raw_after_ceiling:
        parts.append(
            f"Further reduced from ${raw_after_ceiling:,.0f} to preserve "
            f"${MARGIN_BUFFER_USD:,.0f} margin buffer."
        )
    if held > 0:
        parts.append(f"Already hold {held:g} shares.")
    return " ".join(parts)


def _save(rec, current):
    with get_conn() as conn:
        cursor = conn.execute("""
            INSERT INTO trade_recommendations
            (signal_item_id, ticker, direction, conviction, account, action,
             recommended_dollars, recommended_shares, reference_price,
             current_shares, reasoning)
            VALUES
            (:signal_item_id, :ticker, :direction, :conviction, :account, :action,
             :recommended_dollars, :recommended_shares, :reference_price,
             :current_shares, :reasoning)
        """, rec)
        rec["id"] = cursor.lastrowid
    rec["_current"] = current
    return rec


def format_for_pushover(rec):
    title = f"Hedgeye: {rec['direction']} {rec['ticker']} -- {rec['conviction']}"
    lines = []
    current = rec.get("_current")
    if current and current["shares"]:
        pl_pct = current["total_gl_pct"]
        pl_str = f"{pl_pct:+.2f}%" if pl_pct is not None else "n/a"
        lines.append(f"Holding: {current['shares']:g} sh = ${current['current_value']:,.0f} ({pl_str})")
    else:
        lines.append("Holding: none")
    if rec["action"] == "SKIP":
        lines.append(f"-> SKIP: {rec['reasoning']}")
    else:
        price = rec["reference_price"]
        shares = rec["recommended_shares"]
        dollars = rec["recommended_dollars"]
        price_str = f"@ ${price:.2f}" if price else ""
        share_str = f"~{shares:g} sh" if shares else "?"
        lines.append(f"-> {rec['action']} ${dollars:,.0f} {rec['ticker']} ({share_str} {price_str}) in {ACCOUNTS[rec['account']]['name']}")
        lines.append(rec["reasoning"])
    lines.append(f"Reply YES rec#{rec['id']} to approve.")
    return title, "\n".join(lines)
