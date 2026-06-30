"""Two-stage Hedgeye quad detector — READ-ONLY + Telegram only. NEVER writes the
canonical quad regime. The QUAD: Telegram bridge (tools/quad_manual.py) remains
the sole canonical writer; this module only *informs* and *proposes*.

Context (2026-06-29, Hedgeye email-only compliance): the dashboard scrape that
used to silently correct mis-parsed email quads is gone, and the research-note
auto-apply is now gated (parser_research_notes only calls this unless
QUAD_AUTO_APPLY=1). So the quad moves only on an explicit operator QUAD: confirm.

Stage 1 — EARLY-WARNING (informational): the daily Macro Show / Early Look tone
front-runs the official monthly/quarterly flip by ~a week. When the thematic quad
runs different from the official stored quad for >= STREAK_MIN consecutive
note-days, send ONE heads-up per divergence episode. Never proposes a change.

Stage 2 — OFFICIAL FLIP (change proposal): only when a Quads/GIP Update DECK email
is parsed (product == 'quads_gip' with a tilt) do we propose the monthly+quarterly
destination → operator applies via QUAD:. Throttled once per deck (message_id).
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime

log = logging.getLogger(__name__)

THEMATIC_PRODUCTS = ("early_look", "km_top3")   # daily-tone notes carrying a quad
STREAK_MIN = 3                                   # consecutive note-days to warn
LOOKBACK_DAYS = 21

_EPISODE_KEY = "quad_earlywarn_episode"          # JSON sig of last-warned episode
_LASTRUN_KEY = "quad_earlywarn_last_run"         # ET date string, once/day compute
_PROPOSED_KEY = "quad_flip_last_proposed_msgid"  # dedup deck proposals


# ─────────────────────────── helpers ───────────────────────────

def _set_state(key, value):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO bot_state (key, value, updated_at) VALUES (%s,%s,NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                    (key, value))
        conn.commit()


def _get_state(key):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
        r = cur.fetchone()
    return r[0] if r and r[0] else None


def _num(q):
    """'Quad 4' / 'Q4' / '4' -> 4; None on unrecognized."""
    if q is None:
        return None
    m = re.search(r"[1-4]", str(q))
    return int(m.group(0)) if m else None


def _official():
    from tools.quad_regime import current_quad_regime
    r = current_quad_regime()
    return r.get("monthly_quad"), r.get("quarterly_quad")


def _today_et_iso():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return datetime.utcnow().date().isoformat()


def _send(title, msg):
    try:
        from notifier import send_telegram
        send_telegram(title, msg, priority=1)
        return True
    except Exception as e:
        log.warning("quad_detector send failed: %s", e)
        return False


# ─────────────────── Stage 2 — official-flip proposal ───────────────────

def on_research_note(product, tilt, signal_date, message_id=None) -> dict:
    """Called (gated) from parser_research_notes for any tilt-bearing note.
    Proposes a flip ONLY for a Quads/GIP DECK; daily tones are left to Stage 1.
    Never writes the regime."""
    if product != "quads_gip":
        return {"action": "noted-daily-tone", "product": product}
    if not isinstance(tilt, dict):
        return {"action": "no-tilt"}
    tgt_m, tgt_q = tilt.get("effective_monthly"), tilt.get("effective_quarterly")
    if not (_num(tgt_m) or _num(tgt_q)):
        return {"action": "no-target"}
    if message_id and _get_state(_PROPOSED_KEY) == str(message_id):
        return {"action": "already-proposed", "message_id": message_id}

    off_m, off_q = _official()
    m_from = tilt.get("monthly_effective_from")
    q_from = tilt.get("quarterly_effective_from")
    sug_m = _num(tgt_m) or _num(off_m)
    sug_q = _num(tgt_q) or _num(off_q)

    lines = [f"📩 Quads/GIP deck ({signal_date}) — proposed regime flip:"]
    if _num(tgt_q):
        lines.append(f"  • quarterly → {tgt_q}" + (f" (eff {q_from})" if q_from else " (now)")
                     + (f", from {tilt.get('from_quarterly')}" if tilt.get("from_quarterly") else ""))
    if _num(tgt_m):
        lines.append(f"  • monthly → {tgt_m}" + (f" (eff {m_from})" if m_from else " (now)")
                     + (f", from {tilt.get('from_monthly')}" if tilt.get("from_monthly") else ""))
    lines.append(f"Your official: monthly {off_m} / quarterly {off_q}.")
    lines.append(f"Reply  QUAD: monthly {sug_m} quarterly {sug_q}  to apply, or ignore to keep it.")
    if m_from:
        lines.append(f"(monthly {tgt_m} is forward-dated to {m_from} — you can apply quarterly "
                     f"now and the monthly later.)")
    _send("Quads/GIP — confirm quad?", "\n".join(lines))
    if message_id:
        _set_state(_PROPOSED_KEY, str(message_id))
    return {"action": "proposed", "message_id": message_id,
            "suggested": f"QUAD: monthly {sug_m} quarterly {sug_q}"}


# ─────────────────── Stage 1 — early-warning (front-run) ───────────────────

def _dominant(nums):
    """Most common quad number for a day (tie -> first seen)."""
    return Counter(nums).most_common(1)[0][0] if nums else None


def compute_divergence() -> dict:
    """Read-only. Walk back over note-days; count the consecutive most-recent
    run where the thematic quad differs from BOTH official axes and stays on the
    same divergent quad. Returns a summary dict (no Telegram)."""
    off_m, off_q = _official()
    off_nums = {n for n in (_num(off_m), _num(off_q)) if n}
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT signal_date, quad FROM hedgeye_research_notes
                 WHERE product = ANY(%s) AND quad IS NOT NULL
                   AND signal_date > now()::date - {LOOKBACK_DAYS}
                 ORDER BY signal_date DESC""",
            (list(THEMATIC_PRODUCTS),))
        rows = cur.fetchall()
    by_day: dict = {}
    for d, q in rows:
        n = _num(q)
        if n:
            by_day.setdefault(d, []).append(n)
    streak, div_quad = [], None
    for d in sorted(by_day, reverse=True):
        dq = _dominant(by_day[d])
        if dq in off_nums:
            break                          # tone matches official -> episode ends
        if div_quad is None:
            div_quad = dq
        if dq != div_quad:
            break                          # tone moved to a different quad -> boundary
        streak.append(d)
    return {"official_monthly": off_m, "official_quarterly": off_q,
            "div_quad": div_quad, "streak_days": len(streak),
            "streak_start": (min(streak).isoformat() if streak else None)}


def run_early_warning(force: bool = False) -> str:
    """Daily (throttled). Send ONE heads-up per divergence episode when the daily
    tone has run a different quad for >= STREAK_MIN consecutive note-days. Never
    writes the regime, never proposes a change. Returns a status string."""
    today = _today_et_iso()
    if not force and _get_state(_LASTRUN_KEY) == today:
        return "skip:ran-today"
    _set_state(_LASTRUN_KEY, today)

    r = compute_divergence()
    if not r["div_quad"] or r["streak_days"] < STREAK_MIN:
        return f"skip:streak={r['streak_days']}"

    sig = f"{r['div_quad']}|{r['streak_start']}"
    if not force and _get_state(_EPISODE_KEY) == sig:
        return "skip:already-warned-episode"

    msg = (f"⚠️ Hedgeye daily tone running Quad {r['div_quad']} for {r['streak_days']} "
           f"note-days (since {r['streak_start']}) vs your official monthly "
           f"{r['official_monthly']} / quarterly {r['official_quarterly']}.\n"
           f"The daily tone tends to lead the official flip by ~a week — watch for the "
           f"next Quads/GIP deck. (Informational; nothing changed. The deck will prompt "
           f"a QUAD: confirm.)")
    _send("Hedgeye quad early-warning", msg)
    _set_state(_EPISODE_KEY, sig)
    return f"sent:Quad{r['div_quad']}x{r['streak_days']}"


if __name__ == "__main__":
    import json as _json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(_json.dumps(compute_divergence(), default=str, indent=2))
