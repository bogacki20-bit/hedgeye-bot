"""Morning quad-staleness ping + OK-confirm.

Daily ~6:00am ET: if the quad hasn't been confirmed within QUAD_CONFIRM_MAX_AGE_DAYS
(env, default 1), Telegram the operator. Reply OK stamps bot_state.quad_last_confirmed_at
(does NOT change the quad value). QUAD: flows through the existing bridge unchanged.
The bot never infers or auto-sets the quad — this is only a reminder.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

LAST_CONFIRMED_KEY = "quad_last_confirmed_at"
PING_PENDING_KEY   = "quad_confirm_ping_pending"   # "1" while a ping awaits an OK
LASTRUN_KEY        = "quad_confirm_ping_lastrun"    # ET date — once/day throttle


def _max_age_days() -> int:
    try:
        return int(os.getenv("QUAD_CONFIRM_MAX_AGE_DAYS", "1"))
    except ValueError:
        return 1


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


def _today_et() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return datetime.utcnow().date().isoformat()


def _current_quad():
    """(monthly, quarterly, as_of_date) from the latest hedgeye_quad row, else
    (None, None, None)."""
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT monthly_quad, quarterly_quad, scraped_at FROM hedgeye_quad "
                    "ORDER BY scraped_at DESC LIMIT 1")
        r = cur.fetchone()
    if not r or r[0] is None:
        return (None, None, None)
    return (f"Quad {r[0]}", f"Quad {r[1]}", r[2].date().isoformat() if r[2] else None)


def _confirmed_age_days():
    """Days since the quad was last confirmed. Uses quad_last_confirmed_at; if never
    confirmed, falls back to the quad's own stored date (a fresh QUAD: counts)."""
    ref = None
    lc = _get(LAST_CONFIRMED_KEY)
    if lc:
        try:
            ref = datetime.fromisoformat(lc).date()
        except ValueError:
            ref = None
    if ref is None:
        _, _, asof = _current_quad()
        if asof:
            try:
                ref = date.fromisoformat(asof)
            except ValueError:
                ref = None
    return (date.today() - ref).days if ref is not None else None


def run_morning_ping(force: bool = False) -> str:
    """Once/day (ET). Ping only when the quad hasn't been confirmed within
    QUAD_CONFIRM_MAX_AGE_DAYS (or is unset). Returns a status string. No quad write."""
    today = _today_et()
    if not force and _get(LASTRUN_KEY) == today:
        return "skip:ran-today"
    _set(LASTRUN_KEY, today)

    m, q, asof = _current_quad()
    age = _confirmed_age_days()
    if m is None:
        msg = ("🌅 Quad is not set. Reply `QUAD: <monthly> <quarterly>` to set it "
               "(e.g. `QUAD: 4 4`).")
    elif age is not None and age < _max_age_days():
        return f"skip:fresh(age={age}d < {_max_age_days()}d)"
    else:
        last = _get(LAST_CONFIRMED_KEY)
        when = (last[:10] if last else asof) or "unknown"
        msg = (f"🌅 Quad last confirmed {m}/{q} on {when} — still current? "
               f"Reply **OK** to confirm or `QUAD: <monthly> <quarterly>` to change.")

    _set(PING_PENDING_KEY, "1")
    try:
        from notifier import send_telegram
        send_telegram("Quad check", msg, priority=1)
    except Exception as e:
        log.warning("quad morning ping send failed: %s", e)
        return f"error:{e}"
    return f"sent:age={age}"


def handle_quad_confirm_reply(text):
    """'OK' while a ping is pending -> stamp quad_last_confirmed_at=now. Does NOT
    change the quad value. Returns a reply, or None (so a stray 'OK' with no pending
    ping falls through to other handlers)."""
    if not text:
        return None
    if text.strip().upper() not in ("OK", "OK.", "OKAY", "👍"):
        return None
    if _get(PING_PENDING_KEY) != "1":
        return None
    _set(LAST_CONFIRMED_KEY, datetime.now(timezone.utc).isoformat())
    _set(PING_PENDING_KEY, "")
    m, q, _ = _current_quad()
    return (f"✅ Quad confirmed {m}/{q} as current (value unchanged). "
            f"Next check in {_max_age_days()}d.")
