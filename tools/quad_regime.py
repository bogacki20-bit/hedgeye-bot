"""Canonical Quad-regime read path for the bot.

Operator's design (2026-05-28):
    - CURRENT_QUARTERLY_QUAD_OVERRIDE / CURRENT_MONTHLY_QUAD_OVERRIDE env
      vars are the INPUT path — operator sets them on Railway after the
      monthly / quarterly Macro Show calls.
    - `quad_regime_history` (Postgres) is the CANONICAL log.
    - `current_quad_regime()` is the CANONICAL read path — every alert,
      every action, every outcome stamps via this so the regime tag is
      consistent across tables and slow-changing (operator-managed).
    - `sync_quad_regime_from_env()` is called at scanner / launcher
      startup: if the env-var Quads differ from the latest history row,
      insert a fresh history row so the timeline reflects the change.

Falls back to (Quad 2, Quad 3) — the operator's stated baseline as of
2026-05-28 — if both env vars and history are unavailable, with a log
warning. Same fallback as tools/doctrine.py so existing call sites
don't regress.

Schema reference: migrations/030_ml_foundation.sql.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Last-resort default if neither env nor history nor doctrine produce a
# value. Matches the doctrine.py _DEFAULT_QUAD / operator's stated
# 2026-05-28 baseline (Q2 monthly inside Q3 quarterly).
_FALLBACK_MONTHLY   = "Quad 2"
_FALLBACK_QUARTERLY = "Quad 3"


# ─────────────────────────── normalization ───────────────────────────

def _normalize(q: Optional[str]) -> Optional[str]:
    """'Q3' / 'quad3' / '3' / 'Quad 3' all → 'Quad 3'. None on unrecognized."""
    if not q:
        return None
    s = str(q).strip().lower().replace("quad", "").strip()
    if s in ("1", "2", "3", "4"):
        return f"Quad {s}"
    return None


# ─────────────────────────── canonical read ──────────────────────────

def current_quad_regime() -> dict[str, Optional[str]]:
    """Return {'monthly_quad': X, 'quarterly_quad': Y, 'source': S}.

    Precedence:
      1. quad_regime_history latest row (canonical log)
      2. CURRENT_*_QUAD_OVERRIDE env vars (operator's input path; only
         used when history is empty — typically just on first run after
         the migration lands and before sync_quad_regime_from_env runs)
      3. tools.doctrine fallback (legacy, with the same _DEFAULT_QUAD)
      4. _FALLBACK_* hardcoded constants (with a log warning)

    All four paths return the same shape so the caller never has to
    None-check the individual fields. `source` annotates which path won.
    """
    # 1. quad_regime_history (canonical)
    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT monthly_quad, quarterly_quad, source, effective_at
                      FROM quad_regime_history
                     ORDER BY effective_at DESC, id DESC
                     LIMIT 1
                    """
                )
                row = cur.fetchone()
        if row:
            return {
                "monthly_quad":   _normalize(row[0]) or row[0],
                "quarterly_quad": _normalize(row[1]) or row[1],
                "source":         f"history:{row[2]}",
            }
    except Exception as e:
        log.debug("quad_regime: history lookup failed (%s); falling back to env",
                  e)

    # 2. env vars (operator's input path)
    env_m = _normalize(os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE"))
    env_q = _normalize(os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE"))
    if env_m and env_q:
        return {"monthly_quad": env_m, "quarterly_quad": env_q, "source": "env"}

    # 3. doctrine fallback
    try:
        from tools.doctrine import current_monthly_quad, current_quarterly_quad
        return {
            "monthly_quad":   _normalize(current_monthly_quad())   or _FALLBACK_MONTHLY,
            "quarterly_quad": _normalize(current_quarterly_quad()) or _FALLBACK_QUARTERLY,
            "source":         "doctrine",
        }
    except Exception as e:
        log.debug("quad_regime: doctrine fallback failed (%s)", e)

    # 4. Hardcoded fallback
    log.warning("quad_regime: no source available; using hardcoded fallback "
                "(%s, %s). Set CURRENT_*_QUAD_OVERRIDE or seed "
                "quad_regime_history.", _FALLBACK_MONTHLY, _FALLBACK_QUARTERLY)
    return {
        "monthly_quad":   _FALLBACK_MONTHLY,
        "quarterly_quad": _FALLBACK_QUARTERLY,
        "source":         "fallback",
    }


# ─────────────────────────── input sync ──────────────────────────────

def sync_quad_regime_from_env(notes: Optional[str] = None) -> dict:
    """Compare env-var Quads against quad_regime_history latest; if
    different (or history empty), insert a new history row.

    Designed to be called at bot / launcher / scanner startup. Idempotent
    on re-call — only inserts when there's an actual change. Returns a
    summary dict {action: 'inserted'|'unchanged'|'no-env'|'error',
    monthly_quad, quarterly_quad}.
    """
    env_m = _normalize(os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE"))
    env_q = _normalize(os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE"))
    if not (env_m and env_q):
        return {"action": "no-env",
                "monthly_quad": env_m, "quarterly_quad": env_q}

    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT monthly_quad, quarterly_quad
                      FROM quad_regime_history
                     ORDER BY effective_at DESC, id DESC
                     LIMIT 1
                    """
                )
                latest = cur.fetchone()
                if latest and latest[0] == env_m and latest[1] == env_q:
                    return {"action": "unchanged",
                            "monthly_quad": env_m, "quarterly_quad": env_q}

                cur.execute(
                    """
                    INSERT INTO quad_regime_history
                        (monthly_quad, quarterly_quad, source, notes,
                         effective_at)
                    VALUES (%s, %s, 'startup', %s, NOW())
                    RETURNING id
                    """,
                    (env_m, env_q, notes or "sync_quad_regime_from_env"),
                )
                new_id = cur.fetchone()[0]
                conn.commit()
                log.info("quad_regime: new regime row %d (%s / %s)",
                         new_id, env_m, env_q)
                return {"action": "inserted", "id": new_id,
                        "monthly_quad": env_m, "quarterly_quad": env_q}
    except Exception as e:
        log.warning("quad_regime: sync failed (%s)", e)
        return {"action": "error", "error": str(e),
                "monthly_quad": env_m, "quarterly_quad": env_q}


# ─────────────────────────── unified write path ──────────────────────

# Keys the canonical doctrine reader (tools.doctrine) looks for, plus the
# legacy long keys tools.detect_quads.run() used to write directly. set_quads()
# below populates BOTH so the reader stays correct regardless of which path
# wrote last.
_BOT_STATE_QUARTERLY_KEYS = ("quarterly_quad", "current_quarterly_quad")
_BOT_STATE_MONTHLY_KEYS   = ("monthly_quad", "current_monthly_quad")


def _bot_state_get(cur, key: str) -> Optional[str]:
    cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
    r = cur.fetchone()
    return r[0] if r else None


def _bot_state_set(cur, key: str, value: str) -> None:
    cur.execute(
        """
        INSERT INTO bot_state (key, value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = NOW()
        """,
        (key, value),
    )


def _tactical_note(prev_q, new_q, prev_m, new_m) -> str:
    if new_q and prev_q and new_q != prev_q:
        return ("QUARTERLY rotation — re-evaluate strategic universe and "
                "asset-class caps for the new regime.")
    if new_m and prev_m and new_m != prev_m:
        return ("MONTHLY rotation — tighten/loosen tactical bias and alert "
                "calibration; strategic universe unchanged.")
    return "First detection — baseline established."


def set_quads(monthly_quad: str,
              quarterly_quad: str,
              source: str,
              notes: Optional[str] = None,
              alert_on_change: bool = True) -> dict:
    """Single entry point for persisting a Quad change. Writes:
      - bot_state {monthly_quad, current_monthly_quad,
                   quarterly_quad, current_quarterly_quad,
                   last_quad_detection_at}
      - quad_regime_history (only when at least one Quad actually changed)
    Then, on a real rotation:
      - alerts_fired row (ticker='_QUAD', boundary='quad_rotation')
      - one Telegram push (when alert_on_change is True)

    `source`: 'cron' (detect_quads daily), 'operator' (manual seed), 'startup'
    (sync_quad_regime_from_env), etc — recorded in the history row.

    All bot_state and history writes share one psycopg2 connection / one
    transaction so a partial failure can't leave the reader pointing at a
    different value than the history log.

    Returns:
        {'action':       'inserted'|'unchanged',
         'monthly_quad': X,    'quarterly_quad': Y,
         'prev_monthly': X|None, 'prev_quarterly': Y|None,
         'history_id':   int|None,
         'rotation':     bool,
         'rotation_type':'QUARTERLY'|'MONTHLY'|None}
    """
    m = _normalize(monthly_quad)
    q = _normalize(quarterly_quad)
    if not (m and q):
        raise ValueError(
            f"set_quads: unrecognized Quad input monthly={monthly_quad!r} "
            f"quarterly={quarterly_quad!r}"
        )

    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            # Read previous canonical state — short keys first, fall back
            # to legacy long keys (matches doctrine reader ordering).
            prev_m = _bot_state_get(cur, "monthly_quad") \
                  or _bot_state_get(cur, "current_monthly_quad")
            prev_q = _bot_state_get(cur, "quarterly_quad") \
                  or _bot_state_get(cur, "current_quarterly_quad")

            # Always refresh bot_state (idempotent for unchanged values; the
            # updated_at bump is a useful liveness signal for monitoring).
            for k in _BOT_STATE_MONTHLY_KEYS:
                _bot_state_set(cur, k, m)
            for k in _BOT_STATE_QUARTERLY_KEYS:
                _bot_state_set(cur, k, q)
            _bot_state_set(cur, "last_quad_detection_at",
                            datetime.now(timezone.utc).isoformat())

            rotation = (prev_m is not None and prev_m != m) or \
                       (prev_q is not None and prev_q != q)

            history_id = None
            if rotation or prev_m is None or prev_q is None:
                cur.execute(
                    """
                    INSERT INTO quad_regime_history
                        (monthly_quad, quarterly_quad, source, notes,
                         effective_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (m, q, source, notes),
                )
                history_id = cur.fetchone()[0]

        conn.commit()

    rotation_type = None
    if rotation:
        rotation_type = "QUARTERLY" if (prev_q and prev_q != q) else "MONTHLY"
        if alert_on_change:
            note = _tactical_note(prev_q, q, prev_m, m)
            msg = (
                "🔄 QUAD ROTATION DETECTED\n"
                f"Yesterday: Quarterly {prev_q or '?'} / Monthly {prev_m or '?'}\n"
                f"Today:     Quarterly {q} / Monthly {m}\n"
                f"Rotation type: {rotation_type}\n"
                f"Source: {source}\n"
                f"Tactical adjustment: {note}"
            )
            try:
                import db_pg, datetime as _dt
                db_pg.record_alert(
                    ticker="_QUAD",
                    boundary="quad_rotation",
                    signal_date=_dt.date.today(),
                    recommendation_text=msg,
                    suggested_action="QUAD_ROTATION",
                )
            except Exception as e:
                log.warning("set_quads: could not record _QUAD alert: %s", e)
            try:
                from notifier import send_telegram
                send_telegram("QUAD ROTATION DETECTED", msg, priority=2)
            except Exception as e:
                log.warning("set_quads: telegram push failed: %s", e)

    return {
        "action":         "inserted" if history_id else "unchanged",
        "monthly_quad":   m,
        "quarterly_quad": q,
        "prev_monthly":   prev_m,
        "prev_quarterly": prev_q,
        "history_id":     history_id,
        "rotation":       rotation,
        "rotation_type":  rotation_type,
    }


# ─────────────────────────── manual operator entry ───────────────────

def record_quad_change(monthly_quad: str, quarterly_quad: str,
                       source: str = "operator",
                       notes: Optional[str] = None) -> int:
    """Operator-facing: explicitly insert a new history row at NOW().

    Useful when the operator changes regimes mid-cycle and doesn't want
    to wait for the next scanner startup to run sync_quad_regime_from_env.
    Returns the new row id.
    """
    m = _normalize(monthly_quad)
    q = _normalize(quarterly_quad)
    if not (m and q):
        raise ValueError(f"unrecognized Quad input: monthly={monthly_quad!r} "
                         f"quarterly={quarterly_quad!r}")
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO quad_regime_history
                    (monthly_quad, quarterly_quad, source, notes)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (m, q, source, notes),
            )
            new_id = cur.fetchone()[0]
            conn.commit()
            return new_id


# ─────────────────────────── CLI ─────────────────────────────────────

def _cli(argv=None) -> int:
    import argparse, json
    ap = argparse.ArgumentParser(prog="tools.quad_regime")
    ap.add_argument("command",
                    choices=("show", "sync", "set"),
                    help="show=print current regime; sync=read env and "
                         "insert if changed; set=insert an explicit regime")
    ap.add_argument("--monthly", help="Monthly Quad (for `set`)")
    ap.add_argument("--quarterly", help="Quarterly Quad (for `set`)")
    ap.add_argument("--notes", help="Optional notes for `set`/`sync`")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.command == "show":
        print(json.dumps(current_quad_regime(), indent=2))
        return 0
    if a.command == "sync":
        print(json.dumps(sync_quad_regime_from_env(notes=a.notes), indent=2))
        return 0
    if a.command == "set":
        if not (a.monthly and a.quarterly):
            print("ERROR: set requires --monthly and --quarterly")
            return 2
        new_id = record_quad_change(a.monthly, a.quarterly,
                                     source="operator", notes=a.notes)
        print(json.dumps({"action": "inserted", "id": new_id,
                          "monthly_quad": a.monthly,
                          "quarterly_quad": a.quarterly}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
