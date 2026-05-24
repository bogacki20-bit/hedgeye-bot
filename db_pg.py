# spotgamma stripped from live path 2026-05-24; historical snapshots still ingested for ML
# (alerts_fired.spotgamma_context column preserved; callers now write {} / None.
#  spotgamma_snapshots writes continue via spotgamma_client.py.)
"""
Postgres database layer — built alongside SQLite database.py during migration.

Once email_parser.py and main.py swap their imports from `database` to `db_pg`,
the SQLite-backed database.py can be removed.

Connection priority:
  1. DATABASE_PUBLIC_URL  (set when running locally via `railway run`)
  2. DATABASE_URL         (Railway-internal, used by the deployed bot)

All inserts that should be idempotent use ON CONFLICT clauses so re-runs are safe.
"""

import os
import json
import logging
from contextlib import contextmanager
from datetime import date

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)


# ─────────────────────────── Connection ───────────────────────────

def _resolve_dsn() -> str:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Neither DATABASE_PUBLIC_URL nor DATABASE_URL is set. "
            "Run via `railway run` so Railway injects them."
        )
    return dsn


@contextmanager
def get_conn():
    """Yield a psycopg2 connection. Caller controls transaction (commit/rollback)."""
    conn = psycopg2.connect(_resolve_dsn())
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.close()


_COLUMN_CACHE: dict[tuple[str, str], bool] = {}


def _column_exists(table: str, column: str) -> bool:
    """True if `table.column` exists. Cached per process so migrations
    added later are picked up only on restart (acceptable — schema is
    stable within a run). Used to keep INSERTs safe before additive
    migrations are applied."""
    key = (table, column)
    if key in _COLUMN_CACHE:
        return _COLUMN_CACHE[key]
    ok = False
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                 WHERE table_name = %s AND column_name = %s
                """,
                (table, column),
            )
            ok = cur.fetchone() is not None
    except Exception as e:
        log.debug("column-exists check failed for %s.%s: %s", table, column, e)
        ok = False
    _COLUMN_CACHE[key] = ok
    return ok


# ─────────────────────────── Email lake ───────────────────────────

def save_raw_email(
    message_id: str,
    sender: str,
    subject: str | None,
    received_at,
    html_body: str | None = None,
    text_body: str | None = None,
    imap_uid: str | None = None,
    full_report_url: str | None = None,
    content_status: str = "unknown",
    classified_as: str | None = None,
    raw_size_bytes: int | None = None,
) -> bool:
    """
    Insert an email into hedgeye_emails_raw. Returns True if newly inserted,
    False if message_id was already present (duplicate).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO hedgeye_emails_raw
                  (message_id, imap_uid, sender, subject, received_at,
                   html_body, text_body, full_report_url, content_status,
                   classified_as, raw_size_bytes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING message_id
                """,
                (
                    message_id, imap_uid, sender, subject, received_at,
                    html_body, text_body, full_report_url, content_status,
                    classified_as, raw_size_bytes,
                ),
            )
            inserted = cur.fetchone() is not None
        conn.commit()
        return inserted


def is_email_seen(message_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM hedgeye_emails_raw WHERE message_id = %s",
                (message_id,),
            )
            return cur.fetchone() is not None


def is_imap_uid_seen(imap_uid: str) -> bool:
    """Look up by IMAP UID rather than RFC Message-ID. Useful for early backfill
    paths where the Message-ID may not yet be parsed."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM hedgeye_emails_raw WHERE imap_uid = %s LIMIT 1",
                (imap_uid,),
            )
            return cur.fetchone() is not None


def get_unparsed_emails(limit: int = 100):
    """Return raw emails that haven't been processed by a typed-table parser yet."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM hedgeye_emails_raw
                WHERE parsed_at IS NULL
                ORDER BY received_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def mark_email_parsed(message_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE hedgeye_emails_raw SET parsed_at = NOW() WHERE message_id = %s",
                (message_id,),
            )
        conn.commit()


# ─────────────────────────── IMAP backfill state ───────────────────────────

def update_backfill_state(
    folder: str,
    earliest_uid: int | None = None,
    earliest_date=None,
    latest_uid: int | None = None,
    latest_date=None,
    status: str | None = None,
    total_fetched_delta: int = 0,
    notes: str | None = None,
):
    """Upsert backfill progress. Tracks oldest/newest UIDs seen and run status."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO imap_backfill_state
                  (folder, earliest_uid_seen, earliest_date_seen,
                   latest_uid_seen, latest_date_seen,
                   last_run_at, last_status, total_fetched, notes)
                VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s)
                ON CONFLICT (folder) DO UPDATE SET
                  earliest_uid_seen  = LEAST(COALESCE(EXCLUDED.earliest_uid_seen, imap_backfill_state.earliest_uid_seen),
                                              imap_backfill_state.earliest_uid_seen),
                  earliest_date_seen = LEAST(COALESCE(EXCLUDED.earliest_date_seen, imap_backfill_state.earliest_date_seen),
                                              imap_backfill_state.earliest_date_seen),
                  latest_uid_seen    = GREATEST(COALESCE(EXCLUDED.latest_uid_seen, imap_backfill_state.latest_uid_seen),
                                                 imap_backfill_state.latest_uid_seen),
                  latest_date_seen   = GREATEST(COALESCE(EXCLUDED.latest_date_seen, imap_backfill_state.latest_date_seen),
                                                 imap_backfill_state.latest_date_seen),
                  last_run_at        = NOW(),
                  last_status        = COALESCE(EXCLUDED.last_status, imap_backfill_state.last_status),
                  total_fetched      = imap_backfill_state.total_fetched + %s,
                  notes              = COALESCE(EXCLUDED.notes, imap_backfill_state.notes)
                """,
                (
                    folder, earliest_uid, earliest_date,
                    latest_uid, latest_date,
                    status, total_fetched_delta, notes,
                    total_fetched_delta,
                ),
            )
        conn.commit()


def get_backfill_state(folder: str):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM imap_backfill_state WHERE folder = %s",
                (folder,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


# ─────────────────────────── MFR snapshots ───────────────────────────

def save_mfr_snapshot(ticker: str, snapshot_date, payload: dict,
                      source_endpoint: str = "/v2/asset"):
    """Insert MFR snapshot. Surface fields are denormalized from payload.

    Also extracts the gamma-wall fields out of gammaMetrics into typed
    columns (call_wall_mfr / put_wall_mfr / zero_gamma / absolute_gamma /
    iv30_mfr) per migration 028_mfr_typed_walls.sql. These are None for
    tickers MFR doesn't price options on (commodities, FX, VIX).
    """
    p = payload or {}
    range_data = (p.get("rangeData") or {})
    # gammaMetrics.gamma — present on US equities with listed options,
    # absent (null) on commodities/FX/VIX/thin tickers. Drill defensively.
    gm = (p.get("gammaMetrics") or {})
    gm_gamma = (gm.get("gamma") if isinstance(gm, dict) else None) or {}
    gm_quote = (gm_gamma.get("quote") if isinstance(gm_gamma, dict) else None) or {}
    call_wall_mfr  = gm_gamma.get("callWallLevel")
    put_wall_mfr   = gm_gamma.get("putWallLevel")
    zero_gamma     = gm_gamma.get("zeroGamma")
    absolute_gamma = gm_gamma.get("absoluteGammaLevel")
    iv30_mfr       = gm_quote.get("iv30")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mfr_snapshots
                  (ticker, snapshot_date, price, range_low, range_high,
                   trend_signal, momentum_signal, hurst, hurst_3mo,
                   iv, rv, daily_pct_change, previous_day_volume,
                   call_wall_mfr, put_wall_mfr, zero_gamma,
                   absolute_gamma, iv30_mfr,
                   full_payload, fetched_at, source_endpoint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, NOW(), %s)
                ON CONFLICT (ticker, snapshot_date) DO UPDATE SET
                  price             = EXCLUDED.price,
                  range_low         = EXCLUDED.range_low,
                  range_high        = EXCLUDED.range_high,
                  trend_signal      = EXCLUDED.trend_signal,
                  momentum_signal   = EXCLUDED.momentum_signal,
                  hurst             = EXCLUDED.hurst,
                  hurst_3mo         = EXCLUDED.hurst_3mo,
                  iv                = EXCLUDED.iv,
                  rv                = EXCLUDED.rv,
                  daily_pct_change  = EXCLUDED.daily_pct_change,
                  previous_day_volume = EXCLUDED.previous_day_volume,
                  call_wall_mfr     = EXCLUDED.call_wall_mfr,
                  put_wall_mfr      = EXCLUDED.put_wall_mfr,
                  zero_gamma        = EXCLUDED.zero_gamma,
                  absolute_gamma    = EXCLUDED.absolute_gamma,
                  iv30_mfr          = EXCLUDED.iv30_mfr,
                  full_payload      = EXCLUDED.full_payload,
                  fetched_at        = NOW()
                """,
                (
                    ticker,
                    snapshot_date,
                    p.get("latestPrice"),
                    range_data.get("lowerRange"),
                    range_data.get("upperRange"),
                    p.get("trendSignal"),
                    p.get("momentumSignal"),
                    p.get("hurst"),
                    p.get("hurst3Mo"),
                    p.get("iv"),
                    p.get("rv"),
                    p.get("dailyPercentChange"),
                    p.get("previousDayVolume"),
                    call_wall_mfr,
                    put_wall_mfr,
                    zero_gamma,
                    absolute_gamma,
                    iv30_mfr,
                    json.dumps(payload),
                    source_endpoint,
                ),
            )
        conn.commit()


def get_latest_mfr_snapshot(ticker: str):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM mfr_snapshots
                WHERE ticker = %s
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (ticker,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


# ─────────────────────────── Risk Range writes ───────────────────────────

def save_risk_range_rows(rows: list[dict]) -> int:
    """
    Bulk-insert parsed Risk Range rows into hedgeye_risk_ranges.

    Each row dict must include: ticker, signal_date, source_email_id.
    Optional: trend, buy_trade, sell_trade, prev_close, description.

    Idempotent — re-parsing the same email won't duplicate rows. Existing
    (ticker, signal_date) PKs are updated with the new payload. This means a
    re-parse with corrected logic overwrites bad data without manual cleanup.

    Returns the number of rows written (inserted OR updated).
    """
    if not rows:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO hedgeye_risk_ranges
                  (ticker, signal_date, trend, buy_trade, sell_trade,
                   prev_close, description, source_email_id)
                VALUES (%(ticker)s, %(signal_date)s, %(trend)s,
                        %(buy_trade)s, %(sell_trade)s, %(prev_close)s,
                        %(description)s, %(source_email_id)s)
                ON CONFLICT (ticker, signal_date) DO UPDATE SET
                  trend           = EXCLUDED.trend,
                  buy_trade       = EXCLUDED.buy_trade,
                  sell_trade      = EXCLUDED.sell_trade,
                  prev_close      = EXCLUDED.prev_close,
                  description     = EXCLUDED.description,
                  source_email_id = EXCLUDED.source_email_id,
                  parsed_at       = NOW()
                """,
                rows,
                page_size=100,
            )
        conn.commit()
    return len(rows)


def save_signal_changes(rows: list[dict]) -> int:
    """
    Bulk-insert parsed signal-change rows into hedgeye_signal_changes.

    Each row dict must include: ticker, change_type, signal_date,
    source_email_id. Optional: prev_state, new_state.

    Idempotent on (ticker, change_type, signal_date). Returns rows written.
    """
    if not rows:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                INSERT INTO hedgeye_signal_changes
                  (ticker, change_type, prev_state, new_state, signal_date, source_email_id)
                VALUES (%(ticker)s, %(change_type)s, %(prev_state)s,
                        %(new_state)s, %(signal_date)s, %(source_email_id)s)
                ON CONFLICT (ticker, change_type, signal_date) DO UPDATE SET
                  prev_state      = EXCLUDED.prev_state,
                  new_state       = EXCLUDED.new_state,
                  source_email_id = EXCLUDED.source_email_id,
                  parsed_at       = NOW()
                """,
                rows,
                page_size=100,
            )
        conn.commit()
    return len(rows)


def mark_email_classified(message_id: str, classified_as: str,
                          confidence: float | None = None) -> None:
    """
    Update both classified_as and parsed_at on a raw email row.

    Use this after a typed-table parser successfully processes an email.
    If the parser failed (matched the filter but extracted nothing useful),
    pass classified_as='<type>_parse_failed' so the row doesn't get reprocessed
    in a loop while remaining flagged as failed for inspection.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE hedgeye_emails_raw
                   SET classified_as         = %s,
                       classifier_confidence = COALESCE(%s, classifier_confidence),
                       parsed_at             = NOW()
                 WHERE message_id            = %s
                """,
                (classified_as, confidence, message_id),
            )
        conn.commit()


# ─────────────────────────── Risk Range queries ───────────────────────────

def get_latest_risk_range(ticker: str):
    """Return the most recent Risk Range row for a ticker."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM hedgeye_risk_ranges
                WHERE ticker = %s
                ORDER BY signal_date DESC
                LIMIT 1
                """,
                (ticker,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def get_active_risk_ranges(as_of: date | None = None):
    """Return the most recent Risk Range per ticker as of a given date (default today)."""
    if as_of is None:
        as_of = date.today()
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (ticker) *
                FROM hedgeye_risk_ranges
                WHERE signal_date <= %s
                ORDER BY ticker, signal_date DESC
                """,
                (as_of,),
            )
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────── Alerts ───────────────────────────

def has_alert_fired(ticker: str, boundary: str, signal_date) -> bool:
    """Dedup check — has this exact alert already gone out today?"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM alerts_fired
                WHERE ticker = %s AND boundary = %s AND signal_date = %s
                """,
                (ticker, boundary, signal_date),
            )
            return cur.fetchone() is not None


def record_alert(
    ticker: str,
    boundary: str,
    signal_date,
    range_zone: str | None = None,
    price_at_fire: float | None = None,
    range_at_fire: dict | None = None,
    notification_id: str | None = None,
    recommendation_text: str | None = None,
    suggested_action: str | None = None,
    suggested_dollars: float | None = None,
    framework_alignment: str | None = None,
    hedgeye_context: dict | None = None,
    spotgamma_context: dict | None = None,
    prompt_context_full: dict | None = None,
) -> int | None:
    """Record that an alert fired. Returns new id, or None if duplicate.

    Extended columns (from migration 002) capture the dry-run framework
    context: what the bot recommended, why it was framework-aligned, and
    snapshots of the Hedgeye/SpotGamma reasoning at the moment of alert.
    These let user_actions reference back to the alert for ML training.

    `prompt_context_full` (migration 012) stores the COMPLETE LLM prompt
    context as JSON for ML. The column is included only when it exists,
    so this is safe before the migration is applied (graceful no-op).
    """
    have_pcf = _column_exists("alerts_fired", "prompt_context_full")
    with get_conn() as conn:
        with conn.cursor() as cur:
            if have_pcf:
                cur.execute(
                    """
                    INSERT INTO alerts_fired
                      (ticker, boundary, range_zone, signal_date,
                       price_at_fire, range_at_fire, notification_id,
                       recommendation_text, suggested_action, suggested_dollars,
                       framework_alignment, hedgeye_context, spotgamma_context,
                       prompt_context_full)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, boundary, signal_date) DO NOTHING
                    RETURNING id
                    """,
                    (
                        ticker, boundary, range_zone, signal_date,
                        price_at_fire,
                        json.dumps(range_at_fire) if range_at_fire is not None else None,
                        notification_id,
                        recommendation_text,
                        suggested_action,
                        suggested_dollars,
                        framework_alignment,
                        json.dumps(hedgeye_context) if hedgeye_context is not None else None,
                        json.dumps(spotgamma_context) if spotgamma_context is not None else None,
                        json.dumps(prompt_context_full) if prompt_context_full is not None else None,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO alerts_fired
                      (ticker, boundary, range_zone, signal_date,
                       price_at_fire, range_at_fire, notification_id,
                       recommendation_text, suggested_action, suggested_dollars,
                       framework_alignment, hedgeye_context, spotgamma_context)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, boundary, signal_date) DO NOTHING
                    RETURNING id
                    """,
                    (
                        ticker, boundary, range_zone, signal_date,
                        price_at_fire,
                        json.dumps(range_at_fire) if range_at_fire is not None else None,
                        notification_id,
                        recommendation_text,
                        suggested_action,
                        suggested_dollars,
                        framework_alignment,
                        json.dumps(hedgeye_context) if hedgeye_context is not None else None,
                        json.dumps(spotgamma_context) if spotgamma_context is not None else None,
                    ),
                )
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


def find_alert_by_id(alert_id: int):
    """Fetch a single alerts_fired row by id. Returns dict or None.

    Used by telegram_handler when the user replies with an alert id like
    "A1234 BUY 100" — we look up the alert to associate the user_action
    with the right recommendation context.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ticker, boundary, range_zone, signal_date,
                       fired_at, price_at_fire, range_at_fire,
                       notification_id, recommendation_text, suggested_action,
                       suggested_dollars, framework_alignment,
                       hedgeye_context, spotgamma_context
                FROM alerts_fired
                WHERE id = %s
                """,
                (alert_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


def find_recent_alert_for_ticker(ticker: str, hours: int = 24):
    """Fetch the most recent alerts_fired row for a ticker within the last N hours.

    Used when the user replies with a ticker but no alert id (e.g. "OIH BUY 100")
    — we infer they meant the most recent alert on that ticker.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, ticker, boundary, range_zone, signal_date,
                       fired_at, price_at_fire, range_at_fire,
                       notification_id, recommendation_text, suggested_action,
                       suggested_dollars, framework_alignment,
                       hedgeye_context, spotgamma_context
                FROM alerts_fired
                WHERE ticker = %s
                  AND fired_at >= NOW() - (%s || ' hours')::INTERVAL
                ORDER BY fired_at DESC
                LIMIT 1
                """,
                (ticker, hours),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


def save_user_action(
    ticker: str,
    decision: str,
    alert_id: int | None = None,
    executed: bool = False,
    executed_action: str | None = None,
    executed_dollars: float | None = None,
    executed_shares: float | None = None,
    executed_price: float | None = None,
    account: str | None = None,
    fidelity_confirmation_id: str | None = None,
    notes: str | None = None,
    raw_telegram_text: str | None = None,
) -> int:
    """Insert a user_actions row capturing a decision from the user (typically
    via Telegram reply). Returns new id.

    `decision` is the parsed verb — BUY, SELL, ADD, TRIM, PASS, LATER, OVERRIDE.
    `executed` is True only after the user confirms the trade was placed (in
    Fidelity or wherever) — bot defaults False and the user updates later.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_actions
                  (alert_id, ticker, decision, executed,
                   executed_action, executed_dollars, executed_shares, executed_price,
                   account, fidelity_confirmation_id, notes, raw_telegram_text)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    alert_id, ticker, decision, executed,
                    executed_action, executed_dollars, executed_shares, executed_price,
                    account, fidelity_confirmation_id, notes, raw_telegram_text,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0]


def get_user_action(action_id: int):
    """Fetch a single user_actions row by id. Returns dict or None."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, alert_id, ticker, decided_at, decision, executed,
                       executed_action, executed_dollars, executed_shares,
                       executed_price, account, fidelity_confirmation_id,
                       notes, raw_telegram_text, created_at
                FROM user_actions
                WHERE id = %s
                """,
                (action_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))


def update_user_action_executed(
    action_id: int,
    executed_action: str,
    executed_dollars: float | None = None,
    executed_shares: float | None = None,
    executed_price: float | None = None,
    account: str | None = None,
    fidelity_confirmation_id: str | None = None,
) -> bool:
    """Mark a user_action as executed (i.e. the trade was placed). Used when
    the user follows up later with "DONE A1234 OIH 100sh @ 419.50"."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_actions
                SET executed = TRUE,
                    executed_action = COALESCE(%s, executed_action),
                    executed_dollars = COALESCE(%s, executed_dollars),
                    executed_shares = COALESCE(%s, executed_shares),
                    executed_price = COALESCE(%s, executed_price),
                    account = COALESCE(%s, account),
                    fidelity_confirmation_id = COALESCE(%s, fidelity_confirmation_id)
                WHERE id = %s
                """,
                (
                    executed_action, executed_dollars, executed_shares,
                    executed_price, account, fidelity_confirmation_id,
                    action_id,
                ),
            )
            updated = cur.rowcount
        conn.commit()
        return updated > 0


def save_outcome_followup(
    action_id: int,
    days_after: int,
    price_at_followup: float,
    pnl_dollars: float | None = None,
    pnl_pct: float | None = None,
    notes: str | None = None,
) -> int:
    """Record a 1d/5d/20d outcome for a user_action. Idempotent on
    (action_id, days_after) — re-running updates."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO outcome_followups
                  (action_id, days_after, price_at_followup, pnl_dollars, pnl_pct, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (action_id, days_after) DO UPDATE SET
                  measured_at = NOW(),
                  price_at_followup = EXCLUDED.price_at_followup,
                  pnl_dollars = EXCLUDED.pnl_dollars,
                  pnl_pct = EXCLUDED.pnl_pct,
                  notes = EXCLUDED.notes
                RETURNING id
                """,
                (action_id, days_after, price_at_followup, pnl_dollars, pnl_pct, notes),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0]


# ─────────────────────────── Trade recommendations ───────────────────────────

def save_trade_recommendation(rec: dict) -> int:
    """Insert a trade recommendation; returns new id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trade_recommendations
                  (signal_email_id, ticker, direction, conviction, account, action,
                   recommended_dollars, recommended_shares, reference_price,
                   current_shares, reasoning, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    rec.get("signal_email_id"),
                    rec["ticker"],
                    rec.get("direction"),
                    rec.get("conviction"),
                    rec.get("account"),
                    rec.get("action"),
                    rec.get("recommended_dollars"),
                    rec.get("recommended_shares"),
                    rec.get("reference_price"),
                    rec.get("current_shares"),
                    rec.get("reasoning"),
                    rec.get("status", "proposed"),
                ),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


# ─────────────────────────── Smoke test ───────────────────────────

def smoke_test() -> list[str]:
    """Connect, list tables. Used to verify db_pg.py is wired correctly."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name
                """
            )
            return [r[0] for r in cur.fetchall()]


if __name__ == "__main__":
    print("Connecting to Postgres via db_pg.py...")
    tables = smoke_test()
    print(f"Connected. {len(tables)} tables in public schema:")
    for t in tables:
        print(f"  - {t}")
    print()
    print("db_pg.py is wired up. Existing database.py is untouched.")
    print("Next session: swap imports in main.py and email_parser.py.")
