"""Book staleness guard — one place that answers "how old is the broker book?"

WHY THIS EXISTS (2026-08-16). book_positions is fed ONLY by an operator-run
Fidelity CSV export (_daily_upload.py, or a Telegram upload into
tools/doc_ingest). There is no automated broker feed and no scheduled job, so
the book goes stale silently whenever an export is skipped. On 2026-08-16 it had
been 9 days: every screen, BOOK/CONC line, position target and EOD figure was
computed from 2026-08-07 positions and presented as current. A number computed
from stale data and presented as current is a wrong answer presented
confidently — the recurring failure mode on this project.

CONTRACT: every book-dependent surface STATES the snapshot date, always, and
warns loudly past STALE_AFTER_DAYS. Never silent, even when fresh.

The core is PURE (stale_note / status_line take a date, no DB) so the guard is
unit-testable without a database — an absent DB must never make a staleness
check look green.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)

# Warn when the book is MORE than this many days old. 2 covers a normal
# weekend (Fri export read on Sunday = 2 days) without crying wolf; anything
# beyond it means a weekday export was missed.
STALE_AFTER_DAYS = 2


def age_days(snapshot: date | None, today: date | None = None) -> int | None:
    """Whole days between the book snapshot and `today`. None if unknown."""
    if snapshot is None:
        return None
    t = today or date.today()
    return (t - snapshot).days


def is_stale(snapshot: date | None, today: date | None = None) -> bool:
    """Unknown counts as STALE. A missing snapshot is not a fresh one, and the
    fail-open reading ('no date, carry on') is exactly what this guards."""
    d = age_days(snapshot, today)
    return True if d is None else d > STALE_AFTER_DAYS


def stale_note(snapshot: date | None, today: date | None = None) -> str | None:
    """Short inline note, or None when the book is fresh. ASCII only."""
    if snapshot is None:
        return "book date UNKNOWN"
    d = age_days(snapshot, today)
    if d is None or d <= STALE_AFTER_DAYS:
        return None
    return "book %d days old (%s)" % (d, snapshot)


def status_line(snapshot: date | None, today: date | None = None) -> str:
    """The line every book-dependent surface prints. ALWAYS states the date.

    Fresh:  "BOOK as of 2026-08-15 (1d old)"
    Stale:  "!! STALE BOOK: as of 2026-08-07, 9 days old -- positions, weights
             and % figures below are from that date, NOT today. Re-run
             _daily_upload.py after exporting from Fidelity."
    """
    if snapshot is None:
        return ("!! BOOK DATE UNKNOWN -- book_positions is empty or unreadable. "
                "Every position figure below is unverifiable.")
    d = age_days(snapshot, today)
    if d is not None and d <= STALE_AFTER_DAYS:
        return "BOOK as of %s (%dd old)" % (snapshot, d)
    return ("!! STALE BOOK: as of %s, %d days old -- positions, weights and %% "
            "figures below are from that date, NOT today. Re-run "
            "_daily_upload.py after exporting from Fidelity."
            % (snapshot, d))


# ─────────────────────────── DB-backed wrappers ───────────────────────────

def book_snapshot_date() -> date | None:
    """max(snapshot_date) in book_positions, or None. Every read surface uses
    max(snapshot_date), so this IS the date the book speaks for."""
    import db_pg
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT max(snapshot_date) FROM book_positions")
            r = cur.fetchone()
            return r[0] if r else None
    except Exception as e:
        log.warning("book_freshness: snapshot date unreadable (%s)", e)
        return None


def book_status(today: date | None = None) -> dict:
    """{snapshot, days, stale, line} for the current book."""
    snap = book_snapshot_date()
    return {"snapshot": snap,
            "days": age_days(snap, today),
            "stale": is_stale(snap, today),
            "line": status_line(snap, today)}


def book_banner(today: date | None = None) -> str:
    """One-line banner for a text surface. Never empty — silence about the
    book's age is the thing this module exists to prevent."""
    return book_status(today)["line"]
