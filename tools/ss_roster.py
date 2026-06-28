"""Self-updating Signal Strength roster — delta applier (Priority 0, step 3).

Applies parsed SS Add/Remove deltas to ss_roster_history (the canonical roster read
by tools.active_slice). WRITES ONLY ss_roster_history — never MFR/mfr_snapshots
(enroll-never-remove). Idempotent, graceful on no-op deltas, and runs the
count-vs-"N Stocks" tripwire. Design: docs/ss_self_updating_roster.md
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)
_HEADER_RE = re.compile(r"(\d+)\s+Stocks", re.I)


def parse_header_count(subject: str) -> int | None:
    """'Signal Strength Stocks: 80 Stocks (2 Added, 2 Removed)' -> 80."""
    m = _HEADER_RE.search(subject or "")
    return int(m.group(1)) if m else None


def _norm(tickers) -> list[str]:
    out, seen = [], set()
    for t in tickers or []:
        t = (t or "").strip().upper()
        if t and re.fullmatch(r"[A-Z]{1,5}", t) and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def apply_deltas(added, removed, snapshot_date, source_email_id=None,
                 header_count=None) -> dict:
    """Apply Add/Remove to ss_roster_history. Idempotent + graceful; writes ONLY
    ss_roster_history. Squawks if the post-apply roster count != email header."""
    import db_pg
    added, removed = _norm(added), _norm(removed)
    applied_add, applied_remove, skipped = [], [], []
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM ss_roster_history WHERE removed_on IS NULL")
            current = {r[0] for r in cur.fetchall()}

            for t in added:                       # ADD: open a row only if not already open
                if t in current:
                    skipped.append(("add-noop-already-on", t))
                    continue
                cur.execute(
                    "INSERT INTO ss_roster_history (ticker, added_on, add_source, source_email_id) "
                    "VALUES (%s, %s, 'delta', %s)", (t, snapshot_date, source_email_id))
                current.add(t)
                applied_add.append(t)

            for t in removed:                     # REMOVE: close the open row only if open
                if t not in current:
                    skipped.append(("remove-noop-not-on", t))
                    continue
                cur.execute(
                    "UPDATE ss_roster_history SET removed_on=%s, remove_source='delta', "
                    "updated_at=now() WHERE ticker=%s AND removed_on IS NULL",
                    (snapshot_date, t))
                current.discard(t)
                applied_remove.append(t)

            cur.execute("SELECT count(*) FROM ss_roster_history WHERE removed_on IS NULL")
            roster_count = cur.fetchone()[0]
        conn.commit()

    log.info("ss_roster apply: +%s -%s skipped=%d roster=%d header=%s",
             applied_add, applied_remove, len(skipped), roster_count, header_count)
    if header_count is not None and roster_count != header_count:
        try:
            from notifier import send_telegram
            send_telegram("SS Roster",
                          f"⚠️ roster count {roster_count} != email header {header_count} "
                          f"({snapshot_date}). Likely a missed delta — Friday's anchor will "
                          f"correct; check the parse. applied +{applied_add} -{applied_remove}",
                          priority=2)
        except Exception as e:
            log.warning("ss_roster count-mismatch squawk failed: %s", e)
    return {"added": applied_add, "removed": applied_remove, "skipped": skipped,
            "roster_count": roster_count, "header_count": header_count}
