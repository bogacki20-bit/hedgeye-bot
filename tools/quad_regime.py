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
      startup. GATED as of 2026-06-29 (default OFF, QUAD_ENV_SYNC=1 to
      re-enable): the QUAD: Telegram bridge is the canonical input now, so
      env-var overrides no longer auto-insert a history row that could
      overwrite an operator QUAD: set. The CLI `sync` still works (explicit).

Falls back to (Quad 2, Quad 3) — the operator's stated baseline as of
2026-05-28 — if both env vars and history are unavailable, with a log
warning. Same fallback as tools/doctrine.py so existing call sites
don't regress.

Schema reference: migrations/030_ml_foundation.sql.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
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


# ─────────────────────────── staleness ───────────────────────────────
#
# Added 2026-08-02 after the August rollover shipped a WRONG header.
#
# On 8/2 the EOD pack printed "QUAD: monthly=Quad 4 quarterly=Quad 4 (last
# confirm 2026-07-31)". The confirmed state was monthly=Quad 3 / quarterly=
# Quad 4. Nothing malfunctioned: quad_regime_history has no column saying which
# MONTH a monthly_quad is for, so the read path did the only thing it could —
# returned the latest row — and July's monthly silently became August's.
#
# Hedgeye publishes a new monthly Quad every month. So a monthly value last
# confirmed inside a previous calendar month is not a current reading, it is a
# leftover, and the two are indistinguishable downstream unless the read path
# says so. Same argument one level up for the quarterly axis and quarters.
#
# This does NOT derive, default, or advance anything — deriving a Quad is
# exactly the failure being fixed. It carries the confirmed value forward
# untouched and reports that it needs re-confirming. Hedgeye stays the only
# source; the QUAD: command stays the only input.

MARKET_TZ = "America/New_York"

# Every non-history read path returns these so callers get one stable shape.
# No confirmation timestamp is the MOST unconfirmed a Quad can be, so both axes
# report stale rather than silently reading as current.
_NO_CONFIRMATION = {"effective_at": None, "confirmed_on": None,
                    "monthly_stale": True, "quarterly_stale": True,
                    "stale_reason": "no confirmation timestamp"}


def _month_key(d):
    return (d.year, d.month)


def _quarter_key(d):
    return (d.year, (d.month - 1) // 3)


def market_date(value):
    """Any timestamp-ish value -> the ET CALENDAR DATE it belongs to.

    This is the whole ballgame for staleness and it is not cosmetic.
    `quad_regime_history.effective_at` is TIMESTAMPTZ DEFAULT NOW(), and Railway
    runs UTC, so an operator setting the Quad at 20:30 ET on 7/31 is stored as
    00:30 UTC on 8/1. Calling .date() on that tz-aware value returns 2026-08-01
    — an AUGUST date for a JULY action. The monthly axis then reads FRESH on
    8/2 and the whole B1 guard silently does nothing, which is the exact bug it
    exists to catch. Same trap one level up: a 6/30 evening confirmation becomes
    7/1 UTC and a whole quarter rollover goes unflagged.

    Naive values are assumed to already be ET (that is what a `date` from the
    caller means). Strings are parsed so a caller handing us a raw DB text
    column gets the right answer instead of an AttributeError.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(value.strip()[:10])
            except ValueError:
                return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            try:
                from zoneinfo import ZoneInfo
                value = value.astimezone(ZoneInfo(MARKET_TZ))
            except Exception:
                # No tzdata: UTC-5 is still far closer than not converting, and
                # the failure mode being avoided is a late-evening ET
                # confirmation reading as the next calendar day.
                value = value.astimezone(timezone(timedelta(hours=-5)))
        return value.date()
    if isinstance(value, date):
        return value
    return None


def today_market() -> date:
    """Today in ET. `date.today()` is container-local — UTC on Railway — so
    between 20:00 and 24:00 ET it is already tomorrow, and on the last evening
    of a month it disagrees with the trading calendar the Quad is keyed to."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(MARKET_TZ)).date()
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5))).date()


def quad_staleness(effective_at, asof=None) -> dict:
    """Is a Quad confirmed at `effective_at` still current as of `asof`?

    Returns {'monthly_stale', 'quarterly_stale', 'confirmed_on', 'reason'}.
    Both sides are normalised to ET calendar dates first — see market_date().
    `asof` defaults to today in ET.

    Staleness is a CALENDAR question, not an elapsed-days one. A monthly Quad
    confirmed 7/31 is one day old and already stale on 8/1, while one confirmed
    8/1 is stale on nothing until September. Counting days would call the first
    fresh and is the bug this replaces.

    Unknown or unparseable `effective_at` returns stale on both axes: an
    unconfirmable Quad must never present as confirmed.
    """
    out = {"monthly_stale": True, "quarterly_stale": True,
           "confirmed_on": None, "reason": "no confirmation timestamp"}
    asof = market_date(asof) if asof is not None else today_market()
    eff = market_date(effective_at)
    if eff is None or asof is None:
        if effective_at is not None:
            out["reason"] = f"unparseable confirmation timestamp: {effective_at!r}"
        return out
    out["confirmed_on"] = str(eff)[:10]
    out["monthly_stale"] = _month_key(eff) < _month_key(asof)
    out["quarterly_stale"] = _quarter_key(eff) < _quarter_key(asof)
    # A future-dated confirmation is not stale, but it is not normal either.
    if _month_key(eff) > _month_key(asof):
        out["reason"] = f"confirmed {out['confirmed_on']}, AHEAD of {asof}"
        return out
    bad = [k for k, v in (("monthly", out["monthly_stale"]),
                          ("quarterly", out["quarterly_stale"])) if v]
    out["reason"] = (f"{' and '.join(bad)} last confirmed "
                     f"{out['confirmed_on']}, before this "
                     f"{'quarter' if out['quarterly_stale'] else 'month'}"
                     ) if bad else ""
    return out


def last_quad_confirm(cur):
    """The most recent moment a human vouched for the current Quad, from BOTH
    stores that can hold one.

    A quad confirmation has two write paths that land in different places:
      * a value change (QUAD:/CONFIRM bridge, set_quads rotation) appends to
        quad_regime_history.effective_at;
      * the daily 'OK' reply to the 6am ping stamps ONLY
        bot_state['quad_last_confirmed_at'] (tools/quad_confirm.py) — it does
        not append history because the value did not change.
    Every report header used to read only the first store, so weeks of OK
    confirmations were invisible and the header decayed to the last VALUE
    change. 'Last confirm' must mean the later of the two.

    Returns a timestamptz or None. Never raises on a malformed bot_state value
    — an unparseable stamp falls back to the history timestamp alone.
    """
    cur.execute("SELECT max(effective_at) FROM quad_regime_history")
    hist = cur.fetchone()[0]
    cur.execute("SELECT value FROM bot_state WHERE key = 'quad_last_confirmed_at'")
    row = cur.fetchone()
    val = row[0] if row else None
    ok = None
    if isinstance(val, datetime):
        ok = val
    elif isinstance(val, str) and val.strip():
        try:
            ok = datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
        except ValueError:
            ok = None
    if hist is not None and ok is not None:
        # Both stores stamp tz-aware (history is TIMESTAMPTZ, quad_confirm
        # writes an aware isoformat), but guard the naive case anyway.
        if ok.tzinfo is None:
            ok = ok.replace(tzinfo=timezone.utc)
        return max(hist, ok)
    return hist if hist is not None else ok


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
            # effective_at was SELECTed and then thrown away, so every consumer
            # of the canonical read path — price_monitor, decision_engine,
            # quad_detector, the parsers — got July's monthly Quad in August
            # with no way to know. The staleness fields are additive: existing
            # callers keep working unchanged, and new ones can refuse to act on
            # an unconfirmed regime instead of trusting it silently.
            st = quad_staleness(row[3])
            return {
                "monthly_quad":   _normalize(row[0]) or row[0],
                "quarterly_quad": _normalize(row[1]) or row[1],
                "source":         f"history:{row[2]}",
                "effective_at":   row[3],
                "confirmed_on":   st["confirmed_on"],
                "monthly_stale":  st["monthly_stale"],
                "quarterly_stale": st["quarterly_stale"],
                "stale_reason":   st["reason"],
            }
    except Exception as e:
        log.debug("quad_regime: history lookup failed (%s); falling back to env",
                  e)

    # 2. env vars (operator's input path)
    env_m = _normalize(os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE"))
    env_q = _normalize(os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE"))
    if env_m and env_q:
        # Same key set as the history path — an env/doctrine/fallback Quad has
        # no confirmation timestamp at all, which is the most unconfirmed a
        # Quad can be, so both axes report stale.
        return {"monthly_quad": env_m, "quarterly_quad": env_q, "source": "env",
                **_NO_CONFIRMATION}

    # 3. doctrine fallback
    try:
        from tools.doctrine import current_monthly_quad, current_quarterly_quad
        return {
            "monthly_quad":   _normalize(current_monthly_quad())   or _FALLBACK_MONTHLY,
            "quarterly_quad": _normalize(current_quarterly_quad()) or _FALLBACK_QUARTERLY,
            "source":         "doctrine",
            **_NO_CONFIRMATION,
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
        **_NO_CONFIRMATION,
    }


# ─────────────────────────── input sync ──────────────────────────────

def sync_quad_regime_from_env(notes: Optional[str] = None,
                              force: bool = False) -> dict:
    """Compare env-var Quads against quad_regime_history latest; if
    different (or history empty), insert a new history row.

    Designed to be called at bot / launcher / scanner startup. Idempotent
    on re-call — only inserts when there's an actual change. Returns a
    summary dict {action: 'gated'|'inserted'|'unchanged'|'no-env'|'error',
    monthly_quad, quarterly_quad}.

    GATED (2026-06-29, Hedgeye email-only compliance): the QUAD: Telegram bridge
    is the canonical quad INPUT path now. Env-var overrides
    (CURRENT_*_QUAD_OVERRIDE) could otherwise insert a fresh history row at
    scanner/launcher startup and silently overwrite an operator QUAD: set — the
    exact "my quad didn't stay put" failure. Default OFF: automated callers no-op
    unless QUAD_ENV_SYNC=1. The explicit CLI `sync` passes force=True.
    """
    if not force and os.getenv("QUAD_ENV_SYNC") != "1":
        return {"action": "gated",
                "monthly_quad": None, "quarterly_quad": None}
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


# ─────────────────────── research-note tilt → regime ─────────────────

def apply_research_note_tilts(today=None, dry_run: bool = False) -> dict:
    """Advance the live Quad regime from the most recent research-note tilt.

    The Quads/GIP / Early Look parsers stamp the DESTINATION Quad of any
    regime tilt onto hedgeye_research_notes.tilt_target_quads. This reads
    the freshest such row and pushes it into bot_state via set_quads(),
    honouring three rules:

      1. Forward-dated monthly tilts ("Quad 4 for July") only take effect
         on/after their effective-from date — until then the monthly axis
         holds its current value. Quarterly tilts apply immediately.
      2. Operator env overrides (CURRENT_MONTHLY_QUAD_OVERRIDE /
         CURRENT_QUARTERLY_QUAD_OVERRIDE) ALWAYS win — that axis is left at
         the operator's explicit value and never moved by a tilt.
      3. The resulting quad_regime_history row is stamped
         source='hedgeye_research_note' with the research_note id in notes.

    Returns a summary dict; {'action': 'no-tilt'} when nothing to apply.
    """
    from datetime import date as _date
    today = today or _date.today()

    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, signal_date, tilt_target_quads, effective_from_date
                  FROM hedgeye_research_notes
                 WHERE tilt_target_quads IS NOT NULL
                 ORDER BY signal_date DESC, id DESC
                 LIMIT 1
                """
            )
            row = cur.fetchone()
            if not row:
                return {"action": "no-tilt"}
            rn_id, sig_date, tilt, eff_from_col = row

            # Current canonical bot_state (used to hold an axis whose tilt is
            # not yet effective, or which an env override is pinning).
            cur_m = _bot_state_get(cur, "monthly_quad") \
                or _bot_state_get(cur, "current_monthly_quad")
            cur_q = _bot_state_get(cur, "quarterly_quad") \
                or _bot_state_get(cur, "current_quarterly_quad")
    except Exception as e:
        log.warning("apply_research_note_tilts: read failed (%s)", e)
        return {"action": "error", "error": str(e)}

    if not isinstance(tilt, dict):
        return {"action": "no-tilt", "id": rn_id}

    # Fall back to the canonical reader if bot_state has no baseline yet.
    if not (cur_m and cur_q):
        base = current_quad_regime()
        cur_m = cur_m or base.get("monthly_quad")
        cur_q = cur_q or base.get("quarterly_quad")

    def _effective(target, from_iso) -> bool:
        """True if `target` should apply now (no from-date, or reached)."""
        if not target:
            return False
        if not from_iso:
            return True
        try:
            return today >= _date.fromisoformat(str(from_iso)[:10])
        except ValueError:
            return True

    tgt_m = _normalize(tilt.get("effective_monthly"))
    tgt_q = _normalize(tilt.get("effective_quarterly"))
    m_from = tilt.get("monthly_effective_from")
    q_from = tilt.get("quarterly_effective_from")
    # While a forward-dated tilt is pending, hold the axis at the regime the
    # tilt is LEAVING (the pre-tilt value) rather than whatever bot_state
    # currently reads — that keeps the result deterministic even if an
    # earlier note transiently advanced the same axis.
    hold_m = _normalize(tilt.get("from_monthly")) or cur_m
    hold_q = _normalize(tilt.get("from_quarterly")) or cur_q

    new_m = tgt_m if _effective(tgt_m, m_from) else hold_m
    new_q = tgt_q if _effective(tgt_q, q_from) else hold_q

    # Rule 2 — operator env overrides are immovable.
    env_m = _normalize(os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE"))
    env_q = _normalize(os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE"))
    pinned = []
    if env_m:
        new_m = env_m
        pinned.append("monthly")
    if env_q:
        new_q = env_q
        pinned.append("quarterly")

    if not (new_m and new_q):
        return {"action": "incomplete", "id": rn_id,
                "monthly": new_m, "quarterly": new_q}

    pending_monthly = bool(tgt_m and not _effective(tgt_m, m_from)
                           and "monthly" not in pinned and tgt_m != new_m)

    if dry_run:
        return {"action": "dry-run", "id": rn_id,
                "monthly": new_m, "quarterly": new_q,
                "pending_monthly_target": tgt_m if pending_monthly else None,
                "monthly_effective_from": m_from, "pinned": pinned}

    notes = (f"hedgeye_research_note id={rn_id} signal_date={sig_date}; "
             f"tilt monthly={tgt_m or '-'} quarterly={tgt_q or '-'}"
             + (f"; monthly forward-dated to {m_from} (held at {cur_m})"
                if pending_monthly else "")
             + (f"; env-pinned {','.join(pinned)}" if pinned else ""))
    result = set_quads(
        monthly_quad=new_m,
        quarterly_quad=new_q,
        source="hedgeye_research_note",
        notes=notes,
        alert_on_change=True,
    )
    result.update({"id": rn_id, "pinned": pinned,
                   "pending_monthly_target": tgt_m if pending_monthly else None})
    return result


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
                    choices=("show", "sync", "set", "apply-tilts"),
                    help="show=print current regime; sync=read env and "
                         "insert if changed; set=insert an explicit regime; "
                         "apply-tilts=advance regime from the latest "
                         "research-note tilt")
    ap.add_argument("--monthly", help="Monthly Quad (for `set`)")
    ap.add_argument("--quarterly", help="Quarterly Quad (for `set`)")
    ap.add_argument("--notes", help="Optional notes for `set`/`sync`")
    ap.add_argument("--dry-run", action="store_true",
                    help="apply-tilts: compute only, no writes")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.command == "show":
        print(json.dumps(current_quad_regime(), indent=2))
        return 0
    if a.command == "sync":
        # CLI is explicit operator intent -> bypass the QUAD_ENV_SYNC gate.
        print(json.dumps(sync_quad_regime_from_env(notes=a.notes, force=True),
                         indent=2))
        return 0
    if a.command == "apply-tilts":
        print(json.dumps(apply_research_note_tilts(dry_run=a.dry_run),
                         indent=2, default=str))
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
