"""Manual quad bridge — set the GIP quad by hand via Telegram while the Hedgeye
dashboard scraper is retired (compliance: Hedgeye is email-only now).

Same sentinel -> echo -> CONFIRM -> commit pattern as the SS roster upload. Writes the
SAME path the scraper used (hedgeye_quad + quad_regime_history + bot_state.{monthly,
quarterly,current_*}_quad) so doctor check 11b and every quad-gated decision stay
consistent. Writes ONLY the quad tables/bot_state. Hard 1-4 validation; CONFIRM-gated;
15-min TTL. One pending at a time with SS (cross-clear) so CONFIRM can't collide.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)
SENTINEL = "QUAD:"
PENDING_KEY = "quad_pending"
SS_PENDING_KEY = "ss_pending_upload"     # cross-clear so SS/QUAD CONFIRM can't collide
TTL_MIN = 15


def _set_bot_state(key, value):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO bot_state (key, value, updated_at) VALUES (%s, %s, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                    (key, value))
        conn.commit()


def _get_bot_state(key):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
        r = cur.fetchone()
    return r[0] if r and r[0] else None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _save_pending(d):
    _set_bot_state(PENDING_KEY, json.dumps(d))


def _load_pending():
    v = _get_bot_state(PENDING_KEY)
    if not v:
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


def _clear_pending():
    had = _load_pending() is not None
    _set_bot_state(PENDING_KEY, "")
    return had


def _age_min(p):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(p["created_at"])).total_seconds() / 60
    except Exception:
        return 1e9


def _parse_quad(body):
    """Tolerant: 'monthly 1 quarterly 3' | 'M1 Q3' | 'monthly 1, quarterly 3' | '1 3'."""
    s = (body or "").strip()
    mm = re.search(r'm(?:onthly)?\s*[:=]?\s*(\d+)', s, re.I)
    qq = re.search(r'q(?:uarterly)?\s*[:=]?\s*(\d+)', s, re.I)
    if mm and qq:
        return int(mm.group(1)), int(qq.group(1))
    nums = re.findall(r'\d+', s)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])     # bare "1 3" -> monthly, quarterly
    return None, None


def handle_quad_command(text):
    """Telegram QUAD: bridge. Returns a reply if part of the QUAD flow, else None so the
    listener falls through. A QUAD: paste only STAGES; nothing is written until CONFIRM."""
    if not text:
        return None
    s = text.strip()
    up = s.upper()

    if up.startswith(SENTINEL):
        monthly, quarterly = _parse_quad(s[len(SENTINEL):])
        bad = []
        if monthly is None or not (1 <= monthly <= 4):
            bad.append(f"monthly={monthly}")
        if quarterly is None or not (1 <= quarterly <= 4):
            bad.append(f"quarterly={quarterly}")
        if bad:
            return ("🛑 QUAD rejected — monthly AND quarterly must each be 1-4. Got: "
                    + ", ".join(bad) + "\nFormat: QUAD: monthly 1 quarterly 3  (also M1 Q3 / 1 3)")
        _set_bot_state(SS_PENDING_KEY, "")     # one pending at a time: clear any SS stage
        _save_pending({"m": monthly, "q": quarterly, "created_at": _now()})
        return (f"📐 Set monthly Quad {monthly} / quarterly Quad {quarterly}?\n"
                f"Reply CONFIRM to write it (expires in {TTL_MIN} min).")

    if up in ("CONFIRM", "CONFIRM QUAD"):
        p = _load_pending()
        if not p:
            return None                         # no quad staged -> let SS/other handle CONFIRM
        if _age_min(p) > TTL_MIN:
            _clear_pending()
            return "⏱️ QUAD expired (>15 min). Re-send: QUAD: monthly N quarterly N"
        return _commit_quad(p["m"], p["q"])

    if up == "CANCEL":
        return "QUAD set cancelled." if _clear_pending() else None

    return None


def _commit_quad(monthly, quarterly):
    """Write the SAME path the scraper did: hedgeye_quad + quad_regime_history + bot_state."""
    import db_pg
    m, q = f"Quad {monthly}", f"Quad {quarterly}"
    now = datetime.now(timezone.utc)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO hedgeye_quad
            (as_of, as_of_raw, monthly_quad, monthly_label, quarterly_quad, quarterly_label,
             global_quad, global_label, source_url, source, html_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (now, f"manual via Telegram {now:%Y-%m-%d %H:%M}Z", monthly, "manual",
             quarterly, "manual", None, None, "manual_telegram", "manual_telegram", None))
        cur.execute("""INSERT INTO quad_regime_history
            (monthly_quad, quarterly_quad, global_quad, source, notes, effective_at)
            VALUES (%s,%s,%s,%s,%s,NOW())""",
            (m, q, None, "manual_telegram", "manual quad set via Telegram"))
        for k, v in (("monthly_quad", m), ("quarterly_quad", q),
                     ("current_monthly_quad", m), ("current_quarterly_quad", q)):
            cur.execute("INSERT INTO bot_state (key, value, updated_at) VALUES (%s, %s, NOW()) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()", (k, v))
        conn.commit()
    _clear_pending()
    return (f"✅ Quad set: monthly {m} / quarterly {q}. Wrote hedgeye_quad + spine + bot_state "
            f"(doctrine uses this for every decision).")
