"""Monday-evening operator-duties ping (~6:00pm ET).

Assembles a single Telegram reminder from live table state. It ONLY reminds — it
never writes (except its own once/day throttle in bot_state). Every action routes
through the existing gated commands (SS:, QUAD:, the PM loader's CONFIRM, book
ingest). Each item is shown only when actionable; if nothing's actionable it still
sends "✅ All operator items current" so silence never means broken. No LLM.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

LASTRUN_KEY = "operator_ping_lastrun"       # ET date — once/day throttle
DOCTOR_WARN_KEY = "doctor_active_warnings"   # doctor persists here (dormant until wired)
BOOK_STALE_DAYS = 7


def _get(key):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
        r = cur.fetchone()
    return r[0] if r and r[0] else None


def _set(key, val):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO bot_state (key,value,updated_at) VALUES (%s,%s,NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                    (key, val))
        c.commit()


def _today_et() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return datetime.utcnow().date()


def _scalar(sql, args=()):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        r = cur.fetchone()
    return r[0] if r else None


def _iso_week(d: date):
    y, w, _ = d.isocalendar()
    return (y, w)


def _most_recent_friday(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 4) % 7)   # Mon=0 … Fri=4


def build_checklist() -> str:
    """Assemble the Monday operator message from live state. Read-only."""
    today = _today_et()
    todos: list[str] = []

    # • Position Monitor — weekly upload duty. Actionable unless synced this ISO week.
    pm = _scalar("SELECT max(effective_date) FROM bucket_history")
    if pm is None:
        todos.append("📋 Position Monitor — upload this week's PM list "
                     "(last synced: never — hook live, no feed yet)")
    elif _iso_week(pm) != _iso_week(today):
        todos.append(f"📋 Position Monitor — upload this week's PM list "
                     f"(last synced: {pm})")

    # • MFR backlog — non-empty to-add list.
    try:
        from tools.enrollment import compile_backlog
        r = compile_backlog()
        to_add = r.get("to_add") or []
        # Guard: active_count==0 means the MFR watchlist fetch failed (there are
        # ~550 enrolled) — don't false-alarm the whole roster as "to enroll".
        if to_add and r.get("active_count", 0) > 0:
            todos.append(f"🆕 MFR — {len(to_add)} name(s) not yet active; run `MFR BACKLOG` for the list")
    except Exception as e:
        log.warning("operator_checklist: MFR backlog check failed: %s", e)

    # • Quad — last confirmed date + value (stale = actionable).
    quad_footer = None
    try:
        from tools.quad_confirm import _current_quad, _confirmed_age_days, LAST_CONFIRMED_KEY
        qm, qq, asof = _current_quad()
        age = _confirmed_age_days()
        if qm is None:
            todos.append("📐 Quad — not set; reply `QUAD: <monthly> <quarterly>`")
        elif age == 0:
            quad_footer = f"📐 Quad confirmed today ✓ ({qm}/{qq})"
        else:
            when = (_get(LAST_CONFIRMED_KEY) or "")[:10] or asof or "unknown"
            todos.append(f"📐 Quad — {qm}/{qq}, last confirmed {when} — reply OK or `QUAD:` to update")
    except Exception as e:
        log.warning("operator_checklist: quad check failed: %s", e)

    # • SS roster — flag if the most-recent Friday anchor didn't land.
    anchor = _scalar("SELECT max(anchor_date) FROM ss_roster_anchor")
    fri = _most_recent_friday(today)
    if anchor is None or anchor < fri:
        todos.append(f"📊 SS roster — Friday {fri} anchor didn't land "
                     f"(latest: {anchor or 'none'})")

    # • Book — flag if the latest snapshot is > BOOK_STALE_DAYS old.
    book = _scalar("SELECT max(snapshot_date) FROM book_positions")
    if book is None:
        todos.append("💼 Book — no positions loaded; upload a Fidelity export")
    elif (today - book).days > BOOK_STALE_DAYS:
        todos.append(f"💼 Book — snapshot {book} is {(today - book).days}d old; re-upload the Fidelity export")

    # • Doctor — any currently-active warnings (dormant until the doctor persists them).
    dw = _get(DOCTOR_WARN_KEY)
    if dw:
        todos.append(f"🩺 Doctor — active warnings: {dw}")

    header = "🗓️ Monday operator check"
    if not todos:
        tail = f"  {quad_footer}" if quad_footer else ""
        return f"{header} — ✅ All operator items current.{tail}"
    msg = header + ":\n" + "\n".join(todos)
    if quad_footer:
        msg += "\n" + quad_footer
    return msg


def run_operator_ping(force: bool = False) -> str:
    """Once/day (ET) throttle, then send the checklist. Caller (main.py daemon)
    gates on Monday + ~6pm ET. Never writes anything but the throttle key."""
    today = _today_et().isoformat()
    if not force and _get(LASTRUN_KEY) == today:
        return "skip:ran-today"
    _set(LASTRUN_KEY, today)
    msg = build_checklist()
    try:
        from notifier import send_telegram
        send_telegram("Operator check", msg, priority=1)
    except Exception as e:
        log.warning("operator_checklist send failed: %s", e)
        return f"error:{e}"
    return "sent"
