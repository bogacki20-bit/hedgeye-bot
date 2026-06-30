"""Self-updating Signal Strength roster — delta applier + weekly anchor upload.

- apply_deltas(): step 3 — apply parsed SS Add/Remove deltas to ss_roster_history.
- handle_telegram_text(): step 4 — the weekly human anchor. A paste only STAGES;
  nothing is written until CONFIRM. Declarative reconcile to the uploaded list.

WRITES ONLY ss_roster_history (+ the ss_roster_anchor audit row) — never
MFR/mfr_snapshots (enroll-never-remove). Design: docs/ss_self_updating_roster.md
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

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


# ─────────────────────────── Weekly anchor upload (Telegram) ───────────────────────────

SENTINEL = "SS:"               # a roster paste MUST start with this
PENDING_KEY = "ss_pending_upload"
PENDING_TTL_MIN = 15
REPLACE_GUARD_PCT = 50         # removing > this % of the roster needs CONFIRM REPLACE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_date():
    return datetime.now(timezone.utc).date()


def _set_bot_state(key: str, value: str) -> None:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_state (key, value, updated_at) VALUES (%s, %s, NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (key, value))
        conn.commit()


def _get_bot_state(key: str):
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
            r = cur.fetchone()
    return r[0] if r and r[0] else None


def _save_pending(d: dict) -> None:
    _set_bot_state(PENDING_KEY, json.dumps(d))


def _load_pending() -> dict | None:
    v = _get_bot_state(PENDING_KEY)
    if not v:
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


def _clear_pending() -> bool:
    had = _load_pending() is not None
    _set_bot_state(PENDING_KEY, "")
    return had


def _age_min(pending: dict) -> float:
    try:
        t = datetime.fromisoformat(pending.get("created_at"))
        return (datetime.now(timezone.utc) - t).total_seconds() / 60
    except Exception:
        return 1e9  # unknown timestamp -> treat as expired


def _parse_paste(body: str):
    """Split a paste on whitespace/commas, normalize, dedup. Returns (tickers, ignored)."""
    tickers, ignored, seen = [], [], set()
    for tok in re.split(r"[\s,]+", (body or "").strip()):
        tok = tok.strip().upper()
        if not tok:
            continue
        if re.fullmatch(r"[A-Z]{1,5}", tok):
            if tok not in seen:
                seen.add(tok)
                tickers.append(tok)
        else:
            ignored.append(tok)
    return tickers, ignored


def _current_roster_set() -> set:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM ss_roster_history WHERE removed_on IS NULL")
            return {r[0] for r in cur.fetchall()}


def handle_telegram_text(text: str) -> str | None:
    """SS-roster Telegram branch. Returns a reply if this message is part of the SS
    flow (stage / confirm / cancel), else None so the caller falls through to its
    normal handling. A paste only STAGES — nothing is written until CONFIRM."""
    if not text:
        return None
    s = text.strip()
    up = s.upper()

    if up in ("CONFIRM", "CONFIRM REPLACE"):
        pending = _load_pending()
        if not pending:
            return None  # nothing staged -> normal handling (echo)
        if _age_min(pending) > PENDING_TTL_MIN:
            _clear_pending()
            return "⏱️ SS upload expired (>15 min). Re-paste: SS: TICK1 TICK2 ..."
        if pending.get("needs_replace") and up != "CONFIRM REPLACE":
            return ("🛑 That upload removes a large share of the roster. If you REALLY mean "
                    "it, reply CONFIRM REPLACE (not just CONFIRM); otherwise re-paste the full list.")
        return _commit_anchor(pending)

    if up == "CANCEL":
        return "SS upload cancelled." if _clear_pending() else None

    if up.startswith(SENTINEL):
        return _stage_upload(s[len(SENTINEL):])

    return None  # not an SS message


def _stage_upload(body: str) -> str:
    tickers, ignored = _parse_paste(body)
    if not tickers:
        return "No valid tickers in that paste. Re-send as: SS: TICK1 TICK2 ..."
    current = _current_roster_set()
    upload = set(tickers)
    add, rem = sorted(upload - current), sorted(current - upload)
    pct_rem = (len(rem) / len(current) * 100) if current else 0
    needs_replace = bool(current) and (pct_rem > REPLACE_GUARD_PCT or len(upload) < 0.5 * len(current))
    _save_pending({"tickers": sorted(upload), "created_at": _now_iso(),
                   "needs_replace": needs_replace})
    _set_bot_state("quad_pending", "")   # one pending at a time: staging SS clears any QUAD stage

    out = [f"📋 Parsed {len(upload)} tickers"
           + (f" (ignored {len(ignored)}: {ignored[:5]})" if ignored else "") + ".",
           f"Current roster: {len(current)}.  This will REMOVE {len(rem)} and ADD {len(add)}."]
    if rem:
        out.append("  − " + ", ".join(rem[:40]) + (" …" if len(rem) > 40 else ""))
    if add:
        out.append("  + " + ", ".join(add))
    if needs_replace:
        out += [f"🛑 That removes {pct_rem:.0f}% of the roster — looks like a partial/garbled paste.",
                "If you REALLY mean it, reply CONFIRM REPLACE; otherwise re-paste the full list."]
    else:
        out.append("Reply CONFIRM to apply (expires in 15 min).")
    return "\n".join(out)


def _commit_anchor(pending: dict) -> str:
    import db_pg
    from psycopg2.extras import Json
    upload = set(pending["tickers"])
    today = _utc_date()
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM ss_roster_history WHERE removed_on IS NULL")
            current = {r[0] for r in cur.fetchall()}
            add, rem = sorted(upload - current), sorted(current - upload)
            cur.execute(
                "INSERT INTO ss_roster_anchor "
                "(anchor_date, ticker_count, tickers, roster_before, diff_added, diff_removed, note) "
                "VALUES (%s,%s,%s,%s,%s,%s,'telegram_upload') RETURNING id",
                (today, len(upload), Json(sorted(upload)), len(current), Json(add), Json(rem)))
            anchor_id = cur.fetchone()[0]
            for t in add:
                cur.execute("INSERT INTO ss_roster_history (ticker, added_on, add_source, anchor_id) "
                            "VALUES (%s, %s, 'anchor', %s)", (t, today, anchor_id))
            for t in rem:
                cur.execute("UPDATE ss_roster_history SET removed_on=%s, remove_source='anchor', "
                            "anchor_id=%s, updated_at=now() WHERE ticker=%s AND removed_on IS NULL",
                            (today, anchor_id, t))
        conn.commit()
    _clear_pending()
    _set_bot_state("ss_last_anchor_date", today.isoformat())
    return (f"✅ SS anchor set: {len(upload)} names. Delta roster was {len(current)}; "
            f"corrected +{add or 'none'} −{rem or 'none'}.")


# ─────────────────────────── Friday anchor prompt + re-ping (step 5) ───────────────────────────
# Prompt-only — writes ONLY bot_state (ss_last_prompt_date), never the roster.

PROMPT_DATE_KEY = "ss_last_prompt_date"
ANCHOR_FRESH_DAYS = 7
_FRIDAY_MSG = ("📋 Friday Signal Strength roster anchor.\n"
               "Paste this week's FULL SS list, then reply CONFIRM:\n"
               "SS: TICK1 TICK2 TICK3 ...")


def _et_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def _ss_email_parsed_today(now_et) -> bool:
    """True if today's (ET) Signal Strength email is already parsed."""
    import db_pg
    start_utc = now_et.replace(hour=0, minute=0, second=0,
                               microsecond=0).astimezone(timezone.utc)
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s AND classified_as='signal_strength' "
                "AND received_at >= %s LIMIT 1",
                ("%Signal Strength Stocks%", start_utc))
            return cur.fetchone() is not None


def _send_prompt(text: str) -> None:
    try:
        from notifier import send_telegram
        send_telegram("SS Roster", text, priority=1)
    except Exception as e:
        log.warning("ss anchor prompt send failed: %s", e)


def maybe_send_anchor_prompt(now_et=None) -> str:
    """Friday >=6:30pm ET (after that day's SS email is parsed) -> upload prompt;
    a non-Friday with a stale anchor -> once/day reminder. Throttled once/day via
    bot_state.ss_last_prompt_date; reminders STOP once an anchor lands (ss_last_anchor_date
    fresh). Writes ONLY bot_state — never the roster. Returns a short status."""
    if now_et is None:
        try:
            now_et = _et_now()
        except Exception as e:
            log.warning("ss anchor prompt: ET tz unavailable (%s) — skipping", e)
            return "skip:no-tz"
    today = now_et.date().isoformat()
    if _get_bot_state(PROMPT_DATE_KEY) == today:
        return "skip:already-prompted-today"

    last_anchor = _get_bot_state("ss_last_anchor_date")
    days_stale, anchor_fresh = None, False
    if last_anchor:
        try:
            la = datetime.fromisoformat(last_anchor).date()
            days_stale = (now_et.date() - la).days
            anchor_fresh = days_stale < ANCHOR_FRESH_DAYS
        except Exception:
            pass

    is_friday = now_et.weekday() == 4
    after_window = (now_et.hour, now_et.minute) >= (18, 30)   # 6:30pm ET

    if is_friday and after_window:
        if last_anchor == today:
            return "skip:anchored-today"
        if not _ss_email_parsed_today(now_et):
            return "skip:friday-email-not-parsed-yet"   # wait for the day's delta
        _send_prompt(_FRIDAY_MSG)
        _set_bot_state(PROMPT_DATE_KEY, today)
        return "sent:friday-prompt"

    if not is_friday and not anchor_fresh:
        n = f"{days_stale} days" if days_stale is not None else "not set"
        _send_prompt(f"⏰ SS roster anchor is stale ({n}). Upload when you can:\n"
                     "SS: TICK1 TICK2 ...\nRunning on the last anchor + daily deltas meanwhile.")
        _set_bot_state(PROMPT_DATE_KEY, today)
        return "sent:reminder"

    return "skip:nothing-due"
