"""
Trade Recommender
Takes a classified Hedgeye signal (from classifier.py) and the current
portfolio state (from portfolio.py), decides on a sized recommendation,
logs it to SQLite, and returns a Pushover-ready summary.

Sizing (tunable via constants below):
  Best Idea  → 5% of target-account value, capped at $2,500
  Adding     → 3% of target-account value, capped at $1,500
  Reducing   → trim 50% of current position
  Remove     → close 100% of current position
  Monitor    → skip (notify only)
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

SIZING = {
    "Best Idea": {"pct": 0.05, "cap": 2_500},
    "Adding":    {"pct": 0.03, "cap": 1_500},
    "Reducing":  {"trim": 0.50},
    "Remove":    {"trim": 1.00},
}


def recommend_from_signal(item: dict) -> dict | None:
    """
    Build a recommendation from a classified Hedgeye item.
    Returns None if the item isn't a tradeable signal.
    """
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
        # Fall back: pull price from any account holding the symbol
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

    # Block at the rule layer first
    allowed, reason = can_trade(account, direction, instrument="ETF")
    if not allowed:
        rec["reasoning"] = f"Blocked: {reason}"
        return _save(rec, current)

    # Decide action + size from conviction
    if conviction == "Monitor" or conviction not in SIZING:
        rec["reasoning"] = f"Monitor only — no trade for conviction={conviction!r}."
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

    # Best Idea / Adding → open or add
    pct          = SIZING[conviction]["pct"]
    cap          = SIZING[conviction]["cap"]
    acct_value   = account_value(account)
    raw_dollars  = round(min(acct_value * pct, cap), 2)
    dollars      = _respect_buffer(account, raw_dollars, direction)
    shares       = round(dollars / last_price, 3) if last_price and last_price > 0 else None

    rec["action"]              = "SHORT" if direction.lower() == "short" else "BUY"
    rec["recommended_dollars"] = dollars
    rec["recommended_shares"]  = shares
    rec["reasoning"] = _explain_size(
        conviction, ticker, dollars, raw_dollars, acct_value, pct, cap, account, held_shares
    )
    return _save(rec, current)


def _respect_buffer(account, dollars, direction):
    """
    Reserve the $5k margin buffer on the Individual account. We use a
    conservative approximation: cap the trade so it doesn't push us within
    $5k of the account's total equity. (Once we ingest live margin balances
    from the Account_balance CSVs, this can switch to actual margin used.)
    """
    cfg = ACCOUNTS.get(account, {})
    if not cfg.get("margin_buffer"):
        return dollars
    headroom = max(account_value(account) - cfg["margin_buffer"], 0)
    return round(min(dollars, headroom), 2)


def _explain_size(conviction, ticker, dollars, raw, acct_value, pct, cap, account, held):
    name = ACCOUNTS[account]["name"]
    parts = [
        f"{conviction} {ticker}: ${dollars:,.0f} in {name} "
        f"({pct * 100:.0f}% of ${acct_value:,.0f}, cap ${cap:,.0f})."
    ]
    if dollars < raw:
        parts.append(f"Reduced from ${raw:,.0f} to preserve ${MARGIN_BUFFER_USD:,.0f} margin buffer.")
    if held > 0:
        parts.append(f"Already hold {held:g} shares.")
    return " ".join(parts)


def _save(rec, current):
    """Insert into trade_recommendations and return the rec dict."""
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
    rec["_current"] = current  # attach for formatter
    return rec


def format_for_pushover(rec: dict) -> tuple[str, str]:
    """Return (title, message) for Pushover."""
    title = f"Hedgeye: {rec['direction']} {rec['ticker']} — {rec['conviction']}"
    lines = []

    current = rec.get("_current")
    if current and current["shares"]:
        pl_pct = current["total_gl_pct"]
        pl_str = f"{pl_pct:+.2f}%" if pl_pct is not None else "n/a"
        lines.append(
            f"Holding: {current['shares']:g} sh = ${current['current_value']:,.0f} ({pl_str})"
        )
    else:
        lines.append("Holding: none")

    if rec["action"] == "SKIP":
        lines.append(f"→ SKIP: {rec['reasoning']}")
    else:
        price = rec["reference_price"]
        shares = rec["recommended_shares"]
        dollars = rec["recommended_dollars"]
        price_str = f"@ ${price:.2f}" if price else ""
        share_str = f"~{shares:g} sh" if shares else "?"
        lines.append(
            f"→ {rec['action']} ${dollars:,.0f} {rec['ticker']} "
            f"({share_str} {price_str}) in {ACCOUNTS[rec['account']]['name']}"
        )
        lines.append(rec["reasoning"])

    lines.append(f"Reply YES rec#{rec['id']} to approve.")
    return title, "\n".join(lines)
