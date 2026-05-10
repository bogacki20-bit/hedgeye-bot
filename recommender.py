"""
Trade Recommender
Takes a classified Hedgeye signal (from classifier.py) and the current
portfolio state (from portfolio.py), decides on a sized recommendation,
logs it to SQLite, and returns a Pushover-ready summary.

SIZING FRAMEWORK (per Kristian + Hedgeye U Ch3 + framework_quotes_compiled.md)
─────────────────────────────────────────────────────────────────────────────
The trade size is computed from a basis-points-of-account-value (bps) starter
or add, then clamped to a hard real-world per-fill dollar ceiling. The bps
values + ceiling are the CONSTRAINT — the decision engine (Claude API) chooses
the bps within these tiers when proposing a trade. Hardcoded calculation here
is the safety net + the format that gets persisted on trade_recommendations.

  Starter position (first BUY for a ticker):   100 bps  (= 1.0% of account)
  Add to existing position:                     50 bps  (= 0.5%) or 100 bps
  Per-fill ceiling (hard cap, regardless):   $1,000     (real-world risk cap)
  Incremental build:    cap any single fill at ~33% of target position size

  Trim (Reducing conviction):   50% of current position
  Remove (Remove conviction):  100% of current position
  Monitor:                      no trade, notify only

CITATIONS (in corpus_documents):
  - framework_quotes_compiled.md  → Ch3 Lesson 2 "A Refresher On Position Sizing"
  - framework_quotes_compiled.md  → Ch3 Lesson 4 "Buy & Sell Incrementally"
  - hedgeye_u_lesson_transcript / chapter3/a-refresher-on-position-sizing  (Keith's verbatim words)
  - hedgeye_u_lesson_transcript / chapter3/strategy-tactics-buy-sell-incrementally
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

# ─────────────────────────── Sizing constants ───────────────────────────
# These constants are also the OUTPUT SCHEMA for decision_engine.py — when
# Claude proposes a trade, the `bps` field MUST be one of {STARTER_BPS,
# ADD_BPS_LOW, ADD_BPS_HIGH} and the dollar value implied is then clamped
# to PER_FILL_CEILING_USD. Editing these constants ripples through both the
# rule-based recommend_from_signal AND the AI decision engine's output
# validation.

STARTER_BPS         = 100     # First entry / new ticker. 1% of account.
ADD_BPS_LOW         = 50      # Conservative add. 0.5%.
ADD_BPS_HIGH        = 100     # Aggressive add (still in framework). 1%.
PER_FILL_CEILING_USD = 1000   # Hard real-world cap, regardless of bps math.
INCREMENTAL_FILL_CAP_PCT = 0.33  # Single fill ≤ 33% of target — enforces "build
                                # incrementally, not 0→full in one BUY"

# Allowed bps values the decision engine can emit. Schema validation rejects
# anything outside this set.
ALLOWED_BPS = (ADD_BPS_LOW, ADD_BPS_HIGH, STARTER_BPS)


SIZING = {
    # Tier metadata — Claude's `conviction` field maps to one of these keys.
    # `bps_default` is the default size in basis points; the decision engine
    # may override to ADD_BPS_LOW within the same conviction tier.
    "Best Idea": {"bps_default": STARTER_BPS,  "is_close": False, "trim": None},
    "Adding":    {"bps_default": ADD_BPS_LOW,  "is_close": False, "trim": None},
    "Reducing":  {"bps_default": None,         "is_close": False, "trim": 0.50},
    "Remove":    {"bps_default": None,         "is_close": True,  "trim": 1.00},
    "Monitor":   {"bps_default": None,         "is_close": False, "trim": None},
}


def size_from_bps(bps: int, acct_value: float) -> float:
    """Compute the recommended dollar size from a bps target.

    Clamps to PER_FILL_CEILING_USD. Returns the rounded dollar value the
    bot will propose. This is the *single source of truth* for any code
    path that converts a conviction → dollars.

      starter 100 bps on $50k → $500   (under $1K cap)
      starter 100 bps on $200k → $2000 → clamped to $1000 (hits cap)
      add 50 bps on $50k → $250  (under $1K cap)
    """
    if bps not in ALLOWED_BPS:
        raise ValueError(f"bps={bps} not in ALLOWED_BPS={ALLOWED_BPS}")
    raw = acct_value * (bps / 10_000.0)
    return round(min(raw, PER_FILL_CEILING_USD), 2)


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

    # Best Idea / Adding → open or add. Size from bps × account value, clamp
    # to $1K per-fill ceiling. See size_from_bps() and the SIZING FRAMEWORK
    # block at the top of this file for the canonical rule.
    bps          = SIZING[conviction]["bps_default"]
    acct_value   = account_value(account)
    raw_dollars  = size_from_bps(bps, acct_value)
    dollars      = _respect_buffer(account, raw_dollars, direction)
    shares       = round(dollars / last_price, 3) if last_price and last_price > 0 else None

    rec["action"]              = "SHORT" if direction.lower() == "short" else "BUY"
    rec["recommended_dollars"] = dollars
    rec["recommended_shares"]  = shares
    rec["reasoning"] = _explain_size(
        conviction, ticker, dollars, raw_dollars, acct_value, bps, account, held_shares
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


def _explain_size(conviction, ticker, dollars, raw, acct_value, bps, account, held):
    """Build the human-readable reasoning string saved with the recommendation.

    Format: '{conviction} {ticker}: ${dollars} in {acct} ({bps} bps of
    ${acct_value}, per-fill cap ${cap}). [Reduced from $X to preserve $Y
    margin buffer.] [Already hold N shares.]'
    """
    name = ACCOUNTS[account]["name"]
    parts = [
        f"{conviction} {ticker}: ${dollars:,.0f} in {name} "
        f"({bps} bps of ${acct_value:,.0f}, per-fill cap ${PER_FILL_CEILING_USD:,.0f})."
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
