"""Hedgeye ticker inventory tracker — every ticker ever signaled, with Quad regime.

Whenever a Hedgeye email mentions a ticker (Risk Range, RTA, signal change,
Macro Show position table, etc.), call `note_ticker(ticker, source, position,
quad_regime)`. The inventory table gets upserted (mention_count incremented,
last_seen_at updated, position/quad refreshed if newer) and a row is appended
to hedgeye_ticker_history for the audit trail.

Query helpers expose the running inventory:
    - active_tickers()                  -> all currently watched
    - long_tickers(quad="Quad 2")       -> what's Keith long under given regime
    - short_tickers(quad="Quad 2")
    - mention_history(ticker, limit=50) -> audit trail per ticker

Used by:
    - email_parser.py (after classifying a Hedgeye email)
    - parser_risk_range.py (after parsing a Risk Range email)
    - any future ingestor that produces ticker mentions
    - price_monitor.py (uses monitored_tickers VIEW for what to poll)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

log = logging.getLogger(__name__)

# Position canonical values
POS_LONG    = "long"
POS_SHORT   = "short"
POS_NEUTRAL = "neutral"
POS_CLOSED  = "closed"

# Source canonical values — must stay in sync with the subject-classifier
# in email_parser.py (the strings flow through to hedgeye_ticker_history.source).
# Order roughly mirrors product priority / signal volume.
#
# Tier-1 daily-signal products (every Hedgeye email parser routes here):
SOURCE_RISK_RANGE             = "risk_range"
SOURCE_RTA                    = "rta"
SOURCE_SIGNAL_CHANGE          = "signal_change"

# Pro Risk Manager — the $8K/year flagship bundle. Many bundled products
# emit emails under their own branding (Risk Range, RTA, etc) and are
# matched above; emails branded *as Pro Risk Manager* land here.
SOURCE_PRO_RISK_MANAGER       = "pro_risk_manager"

# Sector / specialty Pro products Kristian subscribes to as of 2026-05-07:
SOURCE_RETAIL_PRO             = "retail_pro"
SOURCE_FINANCIALS_SECTOR_PRO  = "financials_sector_pro"
SOURCE_REITS_PRO              = "reits_pro"
SOURCE_CAPITAL_ALLOCATION_PRO = "capital_allocation_pro"
SOURCE_HEDGAI_SIGNALS         = "hedgai_signals"
SOURCE_BITCOIN_TREND_TRACKER  = "bitcoin_trend_tracker"
SOURCE_MARKET_SITUATION_REPORT = "market_situation_report"

# Other Hedgeye content streams (not subscribed standalone but emails arrive
# via bundles or free distribution):
SOURCE_INVESTING_IDEA         = "investing_ideas"
SOURCE_MACRO_SHOW             = "macro_show"
SOURCE_EARLY_LOOK             = "early_look"
SOURCE_ETF_PRO                = "etf_pro"
SOURCE_SIGNAL_STRENGTH        = "signal_strength"

# Catch-alls for classifier-tagged categories the subject regex can't match:
SOURCE_SECTOR_PRO             = "sector_pro"        # generic sector_research without a specific Pro
SOURCE_TRADE_SIGNAL           = "trade_signal"      # classifier-tagged signal w/o Hedgeye-product hint
SOURCE_OTHER                  = "other"


def note_ticker(
    ticker: str,
    source: str,
    position: str | None = None,
    quad_regime: str | None = None,
    price_at_mention: float | None = None,
    message_id: str | None = None,
    signal_email_id: int | None = None,
    metadata: dict | None = None,
) -> bool:
    """Upsert the inventory row for `ticker` and append a history entry.

    Returns True if recorded, False on failure.
    """
    if not ticker:
        return False
    ticker = ticker.upper().strip()

    # Write gate for the mention tables. This is the single write path for
    # hedgeye_ticker_inventory / _history, and monitored_tickers is a VIEW
    # over the inventory — so every token accepted here becomes part of the
    # live price-polling and alert universe (price_monitor
    # get_alert_ticker_universe). 17 parsers plus the LLM classifier call
    # in; several extract from prose/OCR, and the classifier can emit
    # near-misses (APPL). Membership or a live quote decides; the probe
    # failing open is logged, never silent.
    try:
        from tools.symbol_guard import validate_for_storage
        kept, _dropped = validate_for_storage([ticker], f"inventory:{source}")
        if not kept:
            return False
    except Exception as e:
        log.warning("symbol_guard unavailable, noting %s unvalidated: %s",
                    ticker, e)

    try:
        import db_pg
    except ImportError as e:
        log.warning("db_pg unavailable, can't note ticker %s: %s", ticker, e)
        return False

    try:
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                # Upsert the inventory row.
                cur.execute(
                    """
                    INSERT INTO hedgeye_ticker_inventory
                      (ticker, current_position, last_source,
                       quad_at_first_seen, quad_current,
                       mention_count, first_seen_at, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, 1, NOW(), NOW())
                    ON CONFLICT (ticker) DO UPDATE SET
                      current_position = COALESCE(EXCLUDED.current_position, hedgeye_ticker_inventory.current_position),
                      last_source      = EXCLUDED.last_source,
                      quad_current     = COALESCE(EXCLUDED.quad_current, hedgeye_ticker_inventory.quad_current),
                      mention_count    = hedgeye_ticker_inventory.mention_count + 1,
                      last_seen_at     = NOW()
                    """,
                    (ticker, position, source, quad_regime, quad_regime),
                )

                # Append history row.
                import json as _json
                cur.execute(
                    """
                    INSERT INTO hedgeye_ticker_history
                      (ticker, source, position, quad_regime, price_at_mention,
                       message_id, signal_email_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ticker, source, position, quad_regime, price_at_mention,
                        message_id, signal_email_id,
                        _json.dumps(metadata or {}),
                    ),
                )
            conn.commit()
        return True
    except Exception as e:
        log.warning("ticker_inventory.note_ticker(%s, %s) failed: %s",
                    ticker, source, e)
        return False


def note_tickers(
    tickers: Iterable[str],
    source: str,
    position: str | None = None,
    quad_regime: str | None = None,
    message_id: str | None = None,
    signal_email_id: int | None = None,
) -> dict:
    """Note multiple tickers under the same source/position/quad. Returns a summary."""
    seen = set()
    summary = {"tickers": 0, "noted": 0, "failed": 0}
    for t in tickers:
        if not t:
            continue
        t = t.upper().strip()
        if t in seen:
            continue
        seen.add(t)
        summary["tickers"] += 1
        ok = note_ticker(t, source, position=position, quad_regime=quad_regime,
                         message_id=message_id, signal_email_id=signal_email_id)
        if ok:
            summary["noted"] += 1
        else:
            summary["failed"] += 1
    return summary


def active_tickers() -> list[dict]:
    """Return all is_active=true rows in hedgeye_ticker_inventory."""
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, current_position, last_source, quad_current,
                       last_seen_at, mention_count
                FROM hedgeye_ticker_inventory
                WHERE is_active = TRUE
                ORDER BY last_seen_at DESC
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def tickers_by_position(position: str, quad: str | None = None) -> list[dict]:
    """Return tickers currently held in a given position. Optionally filter by Quad regime."""
    import db_pg
    sql = """
        SELECT ticker, current_position, last_source, quad_current,
               last_seen_at, mention_count
        FROM hedgeye_ticker_inventory
        WHERE is_active = TRUE AND current_position = %s
    """
    params = [position]
    if quad:
        sql += " AND quad_current = %s"
        params.append(quad)
    sql += " ORDER BY last_seen_at DESC"

    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def long_tickers(quad: str | None = None) -> list[dict]:
    return tickers_by_position(POS_LONG, quad=quad)


def short_tickers(quad: str | None = None) -> list[dict]:
    return tickers_by_position(POS_SHORT, quad=quad)


def mention_history(ticker: str, limit: int = 50) -> list[dict]:
    """Audit trail of every time a ticker has been mentioned, latest first."""
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT seen_at, source, position, quad_regime,
                       price_at_mention, message_id, signal_email_id, metadata
                FROM hedgeye_ticker_history
                WHERE ticker = %s
                ORDER BY seen_at DESC
                LIMIT %s
                """,
                (ticker.upper().strip(), limit),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def monitored_tickers() -> list[str]:
    """Tickers price_monitor.py should poll. Backed by the SQL VIEW
    `monitored_tickers` which combines active inventory + recent Risk Ranges.
    """
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("SELECT ticker FROM monitored_tickers ORDER BY ticker")
                return [r[0] for r in cur.fetchall()]
            except Exception as e:
                log.debug("monitored_tickers VIEW unavailable: %s", e)
                return []
