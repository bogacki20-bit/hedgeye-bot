"""Position Monitor tier-transition history + the bucket write-hook.

The operator's tier buckets live in ticker_tags.hedgeye_bucket_0629. There is no
automated Position Monitor parser that maintains them (PM emails are image-only),
so this module provides the reusable HOOK any bucket-maintainer calls:

    record_bucket_change(ticker, new_bucket, source_email_id=...) — one ticker
    sync_buckets({ticker: bucket, ...}, source_email_id=...)      — a full mapping

On a real CHANGE (including first sighting and removal, where new_bucket='removed')
it appends a bucket_history row STAMPED with the current stored quad (frozen from
hedgeye_quad at write time; never joined retroactively), and updates ticker_tags.
The quad value itself is NEVER changed here — only read. If the quad is unset the
row is stamped NULL and a warning is logged (never guess). Append-only history.
Python owns all writes.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)


def _current_quad(cur) -> tuple:
    """(quad_monthly, quad_quarterly) from the current stored quad (latest
    hedgeye_quad row), as-stored — NO fallback/guess. (None, None) + warning when
    unset."""
    cur.execute("SELECT monthly_quad, quarterly_quad FROM hedgeye_quad "
                "ORDER BY scraped_at DESC LIMIT 1")
    r = cur.fetchone()
    if not r or r[0] is None or r[1] is None:
        log.warning("bucket_history: quad unset in hedgeye_quad — stamping NULL")
        return (None, None)
    return (f"Quad {r[0]}", f"Quad {r[1]}")


def record_bucket_change(ticker, new_bucket, source_email_id=None,
                         effective_date=None) -> dict | None:
    """Append a bucket_history row IFF the ticker's bucket actually changed
    (first sighting: from None; removal: new_bucket='removed'). Stamps the frozen
    quad, updates ticker_tags.hedgeye_bucket_0629. Returns the transition dict, or
    None when unchanged. No CONFIRM gate (derived from ingested data); the quad
    value is only read, never set."""
    import db_pg
    ticker = (ticker or "").strip().upper()
    if not ticker or not new_bucket:
        return None
    eff = effective_date or date.today()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT hedgeye_bucket_0629 FROM ticker_tags WHERE ticker=%s "
                    "FOR UPDATE", (ticker,))
        row = cur.fetchone()
        cur_bucket = row[0] if row else None
        if cur_bucket == new_bucket:
            return None  # no change -> no history row
        qm, qq = _current_quad(cur)
        cur.execute(
            """INSERT INTO bucket_history
               (ticker, bucket, effective_date, quad_monthly, quad_quarterly,
                source_email_id)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (ticker, new_bucket, eff, qm, qq, source_email_id))
        if new_bucket == "removed":
            cur.execute("UPDATE ticker_tags SET hedgeye_bucket_0629=NULL "
                        "WHERE ticker=%s", (ticker,))
        elif row is not None:
            cur.execute("UPDATE ticker_tags SET hedgeye_bucket_0629=%s "
                        "WHERE ticker=%s", (new_bucket, ticker))
        else:
            cur.execute("INSERT INTO ticker_tags (ticker, hedgeye_bucket_0629) "
                        "VALUES (%s,%s) ON CONFLICT (ticker) DO UPDATE SET "
                        "hedgeye_bucket_0629=EXCLUDED.hedgeye_bucket_0629",
                        (ticker, new_bucket))
        conn.commit()
    log.info("bucket_history: %s %s -> %s (quad %s / %s)",
             ticker, cur_bucket or "new", new_bucket, qm, qq)
    return {"ticker": ticker, "from": cur_bucket, "to": new_bucket,
            "date": str(eff), "quad_monthly": qm, "quad_quarterly": qq}


def sync_buckets(mapping: dict, source_email_id=None, detect_removals=False,
                 effective_date=None) -> dict:
    """Apply a full {ticker: bucket} mapping via record_bucket_change. When
    detect_removals=True, tickers currently in ticker_tags but absent from the
    mapping are recorded as 'removed'. effective_date stamps every transition
    with the REPORT's date (a 7/6 PDF ingested 7/12 records 7/6 — a fact
    without its own date isn't a fact). Returns a summary of transitions."""
    import db_pg
    transitions = []
    for tk, bk in mapping.items():
        t = record_bucket_change(tk, bk, source_email_id=source_email_id,
                                 effective_date=effective_date)
        if t:
            transitions.append(t)
    if detect_removals:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker FROM ticker_tags "
                        "WHERE hedgeye_bucket_0629 IS NOT NULL")
            current = {r[0] for r in cur.fetchall()}
        for tk in sorted(current - {k.strip().upper() for k in mapping}):
            t = record_bucket_change(tk, "removed", source_email_id=source_email_id,
                                     effective_date=effective_date)
            if t:
                transitions.append(t)
    return {"transitions": len(transitions), "detail": transitions}


# ─────────────────────────── MOVES query ───────────────────────────

def recent_moves(days: int = 7) -> list:
    """Bucket transitions in the last `days` (default 7): ticker, from -> to, date,
    and the quad frozen at the time. Read-only."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH ordered AS (
              SELECT ticker, bucket, effective_date, quad_monthly, quad_quarterly,
                     LAG(bucket) OVER (PARTITION BY ticker
                                       ORDER BY effective_date, id) AS prev_bucket
              FROM bucket_history)
            SELECT ticker, prev_bucket, bucket, effective_date,
                   quad_monthly, quad_quarterly
              FROM ordered
             WHERE effective_date >= CURRENT_DATE - %s
             ORDER BY effective_date DESC, ticker
            """,
            (days,))
        cols = ("ticker", "from", "to", "date", "quad_monthly", "quad_quarterly")
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def handle_moves_command(text):
    """Telegram: 'MOVES' or 'MOVES <n>' -> list bucket transitions in the last n
    days (default 7). Read-only. Returns None if not the command."""
    if not text:
        return None
    parts = text.strip().split()
    if not parts or parts[0].upper() != "MOVES":
        return None
    days = 7
    if len(parts) > 1 and parts[1].isdigit():
        days = int(parts[1])
    moves = recent_moves(days)
    if not moves:
        return f"📊 No bucket transitions in the last {days} days."
    lines = [f"📊 Bucket moves — last {days} days ({len(moves)}):"]
    for m in moves:
        q = (f"{m['quad_monthly'] or '?'}/{m['quad_quarterly'] or '?'}")
        lines.append(f"  {m['ticker']:<8} {str(m['from'] or 'new'):<14} → "
                     f"{m['to']:<14} {m['date']}  (quad {q})")
    return "\n".join(lines)
