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
from datetime import date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)


# ─────────────────────────── Connection ───────────────────────────

def _load_dotenv_fallback() -> None:
    """Local-run convenience: if the DB env vars are missing, read the repo
    .env once (KEY=VALUE lines, os.environ.setdefault — never overrides real
    env). Railway sets the vars directly, so this is a no-op when deployed."""
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        with open(env_path) as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


def _resolve_dsn() -> str:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        _load_dotenv_fallback()
        dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Neither DATABASE_PUBLIC_URL nor DATABASE_URL is set (and no .env "
            "found next to db_pg.py). Run via `railway run` or fill .env."
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


def with_db_retry(fn, attempts: int = 3, base_delay: float = 1.0):
    """Run fn() and return its result, retrying ONLY transient connection
    failures — psycopg2 OperationalError / InterfaceError (a connect timeout or
    'server closed the connection unexpectedly' mid-operation, both of which we
    have seen against the Railway proxy). Backoff is base_delay * 2**i between
    tries (1s, 2s, 4s, ...); the last error is re-raised after the final attempt.

    Data/logic errors (ProgrammingError, IntegrityError, DataError, ...) are NOT
    caught here — real bugs must surface immediately, unretried.

    Callers wrap their WHOLE connect+execute+commit block in fn so a mid-op drop
    re-runs the entire unit. That is safe because every signal-tool _persist
    upserts via ON CONFLICT, so a retried persist cannot create duplicate rows.
    """
    import time
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            last = e
            if i == attempts - 1:
                raise
            log.warning("db_pg: transient DB error (attempt %d/%d) — retrying in "
                        "%.1fs: %s", i + 1, attempts, base_delay * (2 ** i), str(e)[:120])
            time.sleep(base_delay * (2 ** i))
    raise last  # unreachable: loop returns or raises above


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
    lt_range_data = (p.get("ltRangeData") or {})
    # 083 (2026-08-26): MFR PUBLISHES the position-on-range for both tiers —
    # rangeData.positionOnRange (the dashboard's short-term %) and
    # ltRangeData.positionOnRange. The bot re-derived it for months and had
    # nothing to check the derivation against (the HYG 1.29-vs-0.89 defect).
    mfr_pos_short = range_data.get("positionOnRange")
    mfr_pos_long = lt_range_data.get("positionOnRange")
    rp_source = "mfr-published" if mfr_pos_short is not None else "derived-mfr"
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
                   mfr_pos_short, mfr_pos_long, rp_source,
                   full_payload, fetched_at, source_endpoint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
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
                  mfr_pos_short     = EXCLUDED.mfr_pos_short,
                  mfr_pos_long      = EXCLUDED.mfr_pos_long,
                  rp_source         = EXCLUDED.rp_source,
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
                    mfr_pos_short,
                    mfr_pos_long,
                    rp_source,
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


# ─────────────────────────── Risk Range staleness gate ─────────────────────
#
# HARD RULE (operator, 2026-07-06): a Risk Range row older than
# RR_MAX_AGE_DAYS *trading* days must NEVER be presented as a live/current
# range — no percent-of-RR math, no zone, no narrative, no divergence flag.
# Older rows may only be omitted or rendered explicitly dated
# ("last RR 6/8: $163-$174 (stale)").
#
# Why here: Keith's daily RR email is the source; when a name drops out of
# his rotation (or the parser silently drops a section — see the FX/commodity
# cohort frozen at 2026-02-26) its last row would otherwise be served forever
# as "current" by every ORDER BY signal_date DESC LIMIT 1 reader. This gate is
# the single chokepoint db_pg exposes; `_gate_rr_row` is default-deny — it
# BLANKS buy_trade/sell_trade/trend on a stale row (so a consumer that forgets
# to check the flag still cannot run live math on it) and stashes the raw
# values under stale_* for dated rendering.

def rr_max_age_tdays() -> int:
    """Freshness window for Risk Ranges, in trading days. env RR_MAX_AGE_DAYS
    (default 5)."""
    try:
        return max(1, int(os.environ.get("RR_MAX_AGE_DAYS", "5")))
    except (TypeError, ValueError):
        return 5


def trading_days_between(d0, d1) -> int:
    """Trading days (Mon-Fri; holidays ignored) from d0 to d1. Signed:
    negative when d1 < d0. A weekend-only span is 0. Accepts date or
    datetime for either arg."""
    if d0 is None or d1 is None:
        return 0
    if isinstance(d0, datetime):
        d0 = d0.date()
    if isinstance(d1, datetime):
        d1 = d1.date()
    lo, hi, sign = (d0, d1, 1) if d0 <= d1 else (d1, d0, -1)
    days = 0
    cur = lo
    one = timedelta(days=1)
    while cur < hi:
        cur += one
        if cur.weekday() < 5:      # 0-4 = Mon-Fri
            days += 1
    return days * sign


def risk_range_age(signal_date, as_of=None):
    """(age_tdays, is_stale) for a Risk Range signal_date. is_stale is True
    when the row is strictly older than rr_max_age_tdays() trading days.
    Returns (None, False) when signal_date is None — an undated row cannot be
    aged, so it is treated as not-stale (callers still see age_tdays=None)."""
    if signal_date is None:
        return (None, False)
    if as_of is None:
        # UTC: signal_date comes from hedgeye_emails_raw.received_at, which is
        # timestamptz, so received_at.date() is already a UTC calendar date.
        # A local as_of would compare a UTC date against a local one and
        # understate age by a day during the evening.
        as_of = datetime.now(timezone.utc).date()
    age = trading_days_between(signal_date, as_of)
    return (age, age > rr_max_age_tdays())


def _gate_rr_row(row, as_of=None):
    """Default-deny staleness gate for a single Risk Range dict. Always stamps
    `age_tdays` + `stale`. When stale, blanks buy_trade/sell_trade/trend and
    moves the raw values to stale_buy_trade/stale_sell_trade/stale_trend so the
    composer can render an explicitly-dated breadcrumb but no consumer can run
    live math. Returns the same dict (mutated) for chaining; None passes through."""
    if not row:
        return row
    age, stale = risk_range_age(row.get("signal_date"), as_of)
    row["age_tdays"] = age
    row["stale"] = stale
    if stale:
        row["stale_buy_trade"] = row.get("buy_trade")
        row["stale_sell_trade"] = row.get("sell_trade")
        row["stale_trend"] = row.get("trend")
        row["buy_trade"] = None
        row["sell_trade"] = None
        row["trend"] = None
    return row


# ─────────────────────────── Risk Range queries ───────────────────────────

def get_latest_risk_range(ticker: str):
    """Return the most recent Risk Range row for a ticker (staleness-gated)."""
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
            return _gate_rr_row(dict(row)) if row else None


def get_active_risk_ranges(as_of: date | None = None):
    """Return the most recent Risk Range per ticker as of a given date (default
    today), each staleness-gated (stale rows keep their identity but have blank
    buy_trade/sell_trade/trend + stale_* breadcrumbs)."""
    if as_of is None:
        as_of = datetime.now(timezone.utc).date()   # match signal_date's UTC basis
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
            return [_gate_rr_row(dict(r), as_of) for r in cur.fetchall()]


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
            new_id = row[0] if row else None
            # 2026-05-28: stamp Quad regime on the new row. Uses the
            # canonical reader in tools.quad_regime which respects
            # quad_regime_history first, then env vars. Best-effort —
            # any failure leaves the alert intact with NULL regime
            # rather than breaking the alert pipeline. Populates BOTH
            # the legacy single `quad_regime` column (migration 009)
            # and the new split monthly_quad / quarterly_quad columns
            # (migration 030) so historical query surfaces continue
            # working alongside the new ML query path.
            if new_id is not None:
                try:
                    from tools.quad_regime import current_quad_regime
                    r = current_quad_regime()
                    mq = r.get("monthly_quad")
                    qq = r.get("quarterly_quad")
                    legacy = f"{qq} / {mq}" if (mq and qq) else (qq or mq)
                    # Build the UPDATE based on which columns exist so a
                    # pre-migration env doesn't break.
                    sets, vals = [], []
                    if _column_exists("alerts_fired", "quad_regime"):
                        sets.append("quad_regime = %s");   vals.append(legacy)
                    if _column_exists("alerts_fired", "monthly_quad"):
                        sets.append("monthly_quad = %s");  vals.append(mq)
                    if _column_exists("alerts_fired", "quarterly_quad"):
                        sets.append("quarterly_quad = %s"); vals.append(qq)
                    if sets:
                        vals.append(new_id)
                        cur.execute(
                            f"UPDATE alerts_fired SET {', '.join(sets)} "
                            f"WHERE id = %s",
                            vals,
                        )
                except Exception as e:
                    # Log but don't fail the alert.
                    import logging
                    logging.getLogger(__name__).debug(
                        "alerts_fired quad stamp failed for id=%s: %s",
                        new_id, e)
        conn.commit()
        return new_id


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


# ─────────────────────────── Scrape ingest (generalized) ───────────────────────────
#
# One HTTP endpoint (/api/scrape_ingest) accepts captures from multiple scheduled
# SKILLs, each tagged with a `source` label. Every capture lands in
# corpus_documents (RAG / full-text). Some sources ALSO route to a typed table:
#   spotgamma_tape         -> spotgamma_tape_reports
#   hedgeye_quad_dashboard -> bot_state (current quads + probabilities)
# Other sources are corpus-only until a router is added. The legacy
# save_tape_report() (flat body, POST /api/tape_report) reshapes onto this path.

def _parse_captured_at(value) -> datetime:
    """Parse the captured_at field from the tape POST body into a tz-aware
    (or naive) datetime. Accepts ISO 8601 with offset or trailing 'Z'."""
    if isinstance(value, datetime):
        return value
    if not value:
        raise ValueError("captured_at is required")
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _insert_corpus_document(cur, *, source, captured_dt, title, raw_text,
                            metadata, document_type="scrape") -> bool:
    """Insert/refresh one corpus_documents row using an existing cursor.

    Idempotent via the existing corpus_documents_unique index. The capture
    timestamp is written into source_ref so multiple captures the same day are
    distinct rows (intraday granularity preserved) while a re-POST of the same
    captured_at updates in place. Returns True if the row was newly inserted."""
    Json = psycopg2.extras.Json
    captured_ref = captured_dt.isoformat()
    full_text = (raw_text or "").strip() or title
    cur.execute(
        """
        INSERT INTO corpus_documents
            (source, source_date, source_ref, document_type,
             page_or_segment, title, full_text, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source, COALESCE(source_ref, ''), source_date,
                     COALESCE(page_or_segment, -1))
        DO UPDATE SET
            title       = EXCLUDED.title,
            full_text   = EXCLUDED.full_text,
            metadata    = EXCLUDED.metadata,
            captured_at = NOW()
        RETURNING (xmax = 0) AS inserted
        """,
        (
            source, captured_dt.date(), captured_ref, document_type,
            None, title, full_text, Json(metadata or {}),
        ),
    )
    row = cur.fetchone()
    return bool(row[0]) if row else False


def _insert_tape_report(cur, captured_dt, src: dict) -> bool:
    """Write the typed spotgamma_tape_reports row from a tape-shaped dict `src`
    (top_volume, top_gamma_notional, top_movers, largest_trades,
    live_flow_sample, spx/ndx/vix, *_concentration, decision_note,
    raw_report_markdown, screenshot_path). Idempotent on captured_at.
    Returns True if newly inserted."""
    Json = psycopg2.extras.Json

    def j(key):
        v = src.get(key)
        return Json(v) if v is not None else None

    cur.execute(
        """
        INSERT INTO spotgamma_tape_reports
            (captured_at, spx, ndx, vix,
             top_volume_json, top_gamma_notional_json, top_movers_json,
             largest_trades_json, live_flow_sample_json,
             bullish_concentration, bearish_concentration, decision_note,
             raw_report_markdown, screenshot_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (captured_at) DO UPDATE SET
            spx = EXCLUDED.spx,
            ndx = EXCLUDED.ndx,
            vix = EXCLUDED.vix,
            top_volume_json = EXCLUDED.top_volume_json,
            top_gamma_notional_json = EXCLUDED.top_gamma_notional_json,
            top_movers_json = EXCLUDED.top_movers_json,
            largest_trades_json = EXCLUDED.largest_trades_json,
            live_flow_sample_json = EXCLUDED.live_flow_sample_json,
            bullish_concentration = EXCLUDED.bullish_concentration,
            bearish_concentration = EXCLUDED.bearish_concentration,
            decision_note = EXCLUDED.decision_note,
            raw_report_markdown = EXCLUDED.raw_report_markdown,
            screenshot_path = EXCLUDED.screenshot_path
        RETURNING (xmax = 0) AS inserted
        """,
        (
            captured_dt,
            src.get("spx"), src.get("ndx"), src.get("vix"),
            j("top_volume"), j("top_gamma_notional"), j("top_movers"),
            j("largest_trades"), j("live_flow_sample"),
            src.get("bullish_concentration"),
            src.get("bearish_concentration"),
            src.get("decision_note"),
            src.get("raw_report_markdown") or None,
            src.get("screenshot_path"),
        ),
    )
    row = cur.fetchone()
    return bool(row[0]) if row else False


def _update_quad_bot_state(cur, captured_dt, metadata: dict) -> dict:
    """For source='hedgeye_quad_dashboard': persist the scraped quads +
    probabilities into bot_state (a durable key/value scratch store).

    IMPORTANT — writes NON-authoritative `scraped_*` keys on purpose:
      * tools/doctrine.py resolves the LIVE regime from bot_state keys
        `quarterly_quad`/`monthly_quad` then the legacy
        `current_quarterly_quad`/`current_monthly_quad` — BEFORE the operator's
        CURRENT_*_QUAD_OVERRIDE env vars. That path feeds price_monitor /
        proactive_scanner trade routing.
      * Writing the scrape into those keys would silently override the operator's
        deliberately-set regime with an automated 3x/day scrape. Not safe to do
        implicitly — especially before the sweep SKILL's payload shape is proven.
      * So we mirror the scrape into `scraped_monthly_quad` /
        `scraped_quarterly_quad` / `scraped_quad_probabilities` /
        `scraped_quad_captured_at`, which nothing reads for routing. To make the
        scrape authoritative, promote these to the canonical keys (one-line
        change) once the operator confirms.
    Also deliberately does NOT touch quad_regime_history (canonical regime log,
    tools/quad_regime.py). Returns the keys written."""
    from tools.quad_regime import _normalize as _norm_quad

    def _quad(*candidates):
        """Normalize a quad value to 'Quad N'. Handles what tools.quad_regime
        accepts ('Quad 3'/'quad3'/'3') plus the 'Q2' abbreviation the dashboard
        scrape may emit. Returns None if no candidate yields a 1–4 quad."""
        import re
        for c in candidates:
            if c is None:
                continue
            n = _norm_quad(c)
            if n:
                return n
            m = re.search(r"[1-4]", str(c))
            if m:
                return f"Quad {m.group(0)}"
        return None

    md = metadata or {}
    monthly = _quad(md.get("monthly_quad"),
                    md.get("current_monthly_quad"), md.get("monthly"))
    quarterly = _quad(md.get("quarterly_quad"),
                      md.get("current_quarterly_quad"), md.get("quarterly"))
    probs = md.get("probabilities") if md.get("probabilities") is not None \
        else md.get("quad_probabilities")

    pairs = []
    if monthly:
        pairs.append(("scraped_monthly_quad", monthly))
    if quarterly:
        pairs.append(("scraped_quarterly_quad", quarterly))
    if probs is not None:
        pairs.append(("scraped_quad_probabilities",
                      probs if isinstance(probs, str) else json.dumps(probs)))
    pairs.append(("scraped_quad_captured_at", captured_dt.isoformat()))

    written = {}
    for k, v in pairs:
        cur.execute(
            """
            INSERT INTO bot_state (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (k, v),
        )
        written[k] = v
    return written


def _insert_sg_levels_from_metadata(cur, captured_dt, metadata: dict,
                                    capture_type: str) -> int:
    """Walk the tape metadata and append one sg_levels row per ticker that
    has at least one numeric level field. Returns the count written.

    Expected shape (one of):
      metadata['sg_levels'] = [{ticker, gamma_flip, call_wall, put_wall, …}, …]
      metadata['levels'][TICKER] = {gamma_flip, call_wall, …}
      metadata flat with TICKER as key — last-resort.

    Any field absence is fine; we only require ticker + at least one
    numeric level. Runs in the same transaction as the corpus write."""
    Json = psycopg2.extras.Json
    md = metadata or {}
    rows: list[dict] = []

    if isinstance(md.get("sg_levels"), list):
        for entry in md["sg_levels"]:
            if isinstance(entry, dict) and entry.get("ticker"):
                rows.append(entry)
    elif isinstance(md.get("levels"), dict):
        for tk, entry in md["levels"].items():
            if isinstance(entry, dict):
                row = dict(entry)
                row.setdefault("ticker", tk)
                rows.append(row)

    written = 0
    for row in rows:
        ticker = (row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        level_fields = {
            "gamma_flip":        row.get("gamma_flip"),
            "call_wall":         row.get("call_wall"),
            "put_wall":          row.get("put_wall"),
            "hedge_wall":        row.get("hedge_wall"),
            "key_gamma_strike":  row.get("key_gamma_strike"),
        }
        if not any(v is not None for v in level_fields.values()):
            continue
        cur.execute(
            """
            INSERT INTO sg_levels
                (ticker, captured_at, capture_type,
                 gamma_flip, call_wall, put_wall,
                 hedge_wall, key_gamma_strike, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                ticker, captured_dt, capture_type,
                level_fields["gamma_flip"],
                level_fields["call_wall"],
                level_fields["put_wall"],
                level_fields["hedge_wall"],
                level_fields["key_gamma_strike"],
                Json(row),
            ),
        )
        written += 1
    return written


def _route_source_specific(cur, source, captured_dt, metadata, raw_text,
                           screenshot_path) -> dict:
    """Dispatch to per-source typed tables. Runs in the SAME transaction as the
    corpus write. Unknown sources are corpus-only (returns {})."""
    if source == "spotgamma_tape":
        tape_src = dict(metadata or {})
        tape_src.setdefault("raw_report_markdown", raw_text)
        tape_src.setdefault("screenshot_path", screenshot_path)
        return {"tape_inserted": _insert_tape_report(cur, captured_dt, tape_src)}
    if source == "spotgamma_tape_15m":
        # New tape-watcher route (2026-06-10 — work order item 7). DUAL
        # output by design: corpus_documents (the prose, via the upstream
        # _insert_corpus_document call) for ML, sg_levels (the structured
        # levels) for the deterministic alert/decision read path. No
        # alerting from this source yet — silent accumulation for a few
        # days, quality check first. Market-hours gate lives in api.py
        # so out-of-window POSTs are rejected before they reach here.
        sg_count = _insert_sg_levels_from_metadata(
            cur, captured_dt, metadata, capture_type="tape_15m",
        )
        return {"sg_levels_inserted": sg_count}
    if source == "hedgeye_quad_dashboard":
        return {"bot_state_written": _update_quad_bot_state(cur, captured_dt, metadata)}
    return {}


def save_scrape_ingest(body: dict) -> dict:
    """Generalized scrape ingest entry point (POST /api/scrape_ingest).

    Always writes corpus_documents for `source`; ALSO routes to a source-specific
    table when one exists (see _route_source_specific). Corpus write + routing
    share one transaction — all land or none. Returns a summary dict.
    """
    source = (body.get("source") or "").strip()
    if not source:
        raise ValueError("source is required")
    captured_dt = _parse_captured_at(body.get("captured_at"))
    metadata = body.get("metadata") or {}
    raw_text = body.get("raw_text") or ""
    screenshot_path = body.get("screenshot_path")
    title = body.get("title") or f"{source} {captured_dt.isoformat()}"

    with get_conn() as conn:
        with conn.cursor() as cur:
            corpus_inserted = _insert_corpus_document(
                cur, source=source, captured_dt=captured_dt, title=title,
                raw_text=raw_text, metadata=metadata,
            )
            routed = _route_source_specific(
                cur, source, captured_dt, metadata, raw_text, screenshot_path,
            )
        conn.commit()

    return {
        "source": source,
        "captured_at": captured_dt.isoformat(),
        "source_date": captured_dt.date().isoformat(),
        "corpus_inserted": corpus_inserted,
        **routed,
    }


def save_tape_report(report: dict) -> dict:
    """Backward-compat for POST /api/tape_report (flat body shape).

    The tape SKILL sends tape fields at the top level (spx, top_volume, …,
    raw_report_markdown). Reshape onto the generalized scrape_ingest path so
    there's one code path. New callers should use /api/scrape_ingest directly.
    """
    tape_meta = {
        k: report.get(k)
        for k in (
            "spx", "ndx", "vix",
            "top_volume", "top_gamma_notional", "top_movers",
            "largest_trades", "live_flow_sample",
            "bullish_concentration", "bearish_concentration", "decision_note",
        )
        if report.get(k) is not None
    }
    body = {
        "source": "spotgamma_tape",
        "captured_at": report.get("captured_at"),
        "title": f"SG Tape Report {report.get('captured_at')}",
        "metadata": tape_meta,
        "raw_text": report.get("raw_report_markdown") or "",
        "screenshot_path": report.get("screenshot_path") or report.get("screenshot_url"),
    }
    return save_scrape_ingest(body)


# ─────────────────────────── sg_levels (mig 034) ───────────────────────────
#
# Canonical SpotGamma levels table. Populated by the tape watcher (capture_type
# 'tape_15m'), the daily SG email ingest, and the manual operator-paste path.
# Read into both price_monitor (deterministic alert-body suffix) and
# decision_engine (one pre-computed framing line for the prompt). SG refines
# terrain; Hedgeye RR trend decides direction. SG never overrides Keith.

def save_sg_levels(
    *,
    ticker: str,
    capture_type: str,
    gamma_flip=None,
    call_wall=None,
    put_wall=None,
    hedge_wall=None,
    key_gamma_strike=None,
    raw: dict | None = None,
    captured_at: datetime | None = None,
) -> int | None:
    """Append one row to sg_levels. Returns the new row id, or None on
    failure (DB transient errors don't break the caller's main loop)."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sg_levels
                        (ticker, captured_at, capture_type,
                         gamma_flip, call_wall, put_wall,
                         hedge_wall, key_gamma_strike, raw)
                    VALUES (%s, COALESCE(%s, NOW()), %s,
                            %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        (ticker or "").upper(),
                        captured_at, capture_type,
                        gamma_flip, call_wall, put_wall,
                        hedge_wall, key_gamma_strike,
                        json.dumps(raw) if raw is not None else None,
                    ),
                )
                row_id = cur.fetchone()[0]
            conn.commit()
        return row_id
    except Exception as e:
        log.warning("save_sg_levels failed for %s (%s): %s",
                    ticker, capture_type, e)
        return None


def get_latest_sg_levels(ticker: str, *, max_age_hours: int | None = None) -> dict | None:
    """Return the most recent sg_levels row for `ticker` as a flat dict,
    or None if no row exists (or all rows fail the `max_age_hours`
    freshness filter). Keys mirror the column names; `captured_at` is
    an aware UTC datetime, `raw` is the parsed jsonb dict."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if max_age_hours is None:
                    cur.execute(
                        """
                        SELECT id, ticker, captured_at, capture_type,
                               gamma_flip, call_wall, put_wall,
                               hedge_wall, key_gamma_strike, raw
                          FROM sg_levels
                         WHERE ticker = %s
                         ORDER BY captured_at DESC
                         LIMIT 1
                        """,
                        ((ticker or "").upper(),),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, ticker, captured_at, capture_type,
                               gamma_flip, call_wall, put_wall,
                               hedge_wall, key_gamma_strike, raw
                          FROM sg_levels
                         WHERE ticker = %s
                           AND captured_at >= NOW() - %s::interval
                         ORDER BY captured_at DESC
                         LIMIT 1
                        """,
                        ((ticker or "").upper(), f"{int(max_age_hours)} hours"),
                    )
                row = cur.fetchone()
        return dict(row) if row else None
    except Exception as e:
        log.debug("get_latest_sg_levels failed for %s: %s", ticker, e)
        return None


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
