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
    """Insert MFR snapshot. Surface fields are denormalized from payload."""
    p = payload or {}
    range_data = (p.get("rangeData") or {})
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mfr_snapshots
                  (ticker, snapshot_date, price, range_low, range_high,
                   trend_signal, momentum_signal, hurst, hurst_3mo,
                   iv, rv, daily_pct_change, previous_day_volume,
                   full_payload, fetched_at, source_endpoint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
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
) -> int | None:
    """Record that an alert fired. Returns new id, or None if duplicate."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts_fired
                  (ticker, boundary, range_zone, signal_date,
                   price_at_fire, range_at_fire, notification_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, boundary, signal_date) DO NOTHING
                RETURNING id
                """,
                (
                    ticker, boundary, range_zone, signal_date,
                    price_at_fire,
                    json.dumps(range_at_fire) if range_at_fire is not None else None,
                    notification_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


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
