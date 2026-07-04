"""
Telegram bot listener — minimal slice.

Polls Telegram getUpdates in a daemon background thread, replies
"Got it: [text]" to messages from the whitelisted chat_id, and silently
drops everything else.
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("telegram")

API_BASE = "https://api.telegram.org/bot{token}/{method}"
LONG_POLL_TIMEOUT = 30
HTTP_TIMEOUT = LONG_POLL_TIMEOUT + 5
GENERAL_ERROR_SLEEP = 5
CONFLICT_SLEEP = 30
TG_MAX_CHARS = 4096                       # Telegram hard cap per message
HEARTBEAT_INTERVAL = 60                    # min seconds between bot_state heartbeat writes
LISTENER_HEARTBEAT_KEY = "telegram_listener_heartbeat"   # doctor reads this


def _api_get(token, method, params=None, timeout=HTTP_TIMEOUT):
    url = API_BASE.format(token=token, method=method)
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _chunk_message(text, limit=TG_MAX_CHARS):
    """Split a reply into <=limit-char parts on line boundaries so long SCREEN
    output is delivered in pieces instead of being rejected (HTTP 400) and lost."""
    if text is None:
        return []
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(line) > limit:                     # pathological single long line
            if cur:
                chunks.append(cur); cur = ""
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            continue
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def _send_message(token, chat_id, text):
    """Send a reply, chunking to Telegram's 4096-char limit. Send failures are LOUD
    (log status + response body) but never raise — one bad chunk must not abort the
    listener loop."""
    parts = _chunk_message(text)
    for idx, chunk in enumerate(parts):
        tag = f" [{idx + 1}/{len(parts)}]" if len(parts) > 1 else ""
        try:
            _api_get(token, "sendMessage",
                     params={"chat_id": chat_id, "text": chunk}, timeout=10)
            log.info(f"Sent reply to chat {chat_id}{tag} ({len(chunk)} chars).")
        except Exception as e:
            body = getattr(getattr(e, "response", None), "text", "")
            log.error(f"sendMessage failed{tag} ({len(chunk)} chars): {e} {body}".strip())


def _delete_webhook(token):
    try:
        _api_get(token, "deleteWebhook", params={"drop_pending_updates": False}, timeout=10)
        log.info("deleteWebhook called (defensive).")
    except Exception as e:
        log.error(f"deleteWebhook failed (continuing anyway): {e}")


def _drain_pending_updates(token):
    """Fetch any queued updates and return the offset to start polling from."""
    try:
        result = _api_get(token, "getUpdates", params={"timeout": 0}, timeout=15)
        updates = result.get("result", [])
        if not updates:
            log.info("No pending updates to discard.")
            return None
        last_id = updates[-1]["update_id"]
        log.info(
            f"Discarding {len(updates)} pending update(s); starting at offset {last_id + 1}."
        )
        return last_id + 1
    except Exception as e:
        log.error(f"Failed to drain pending updates at startup: {e}")
        return None


# ─────────────────────────── Decision parser ───────────────────────────

import re

# Recognised decision verbs the user can reply with. Lowercased for matching.
# These map to user_actions.decision values; canonical form is uppercase.
DECISION_VERBS = {
    "buy", "sell",
    "add", "trim",
    "long", "short",
    "pass", "skip", "ignore",
    "later", "wait", "hold",
    "override",
    "done", "filled", "executed",
}

# Pattern A: "A1234 ACTION amount?"  e.g. "A1234 BUY 100"
ALERT_ID_PATTERN = re.compile(
    r"^\s*A\s*(?P<alert_id>\d+)\s+(?P<verb>\w+)(?:\s+(?P<rest>.*))?\s*$",
    re.IGNORECASE,
)

# Pattern B: "TICKER ACTION amount?"  e.g. "OIH BUY 100"
TICKER_PATTERN = re.compile(
    r"^\s*(?P<ticker>[A-Z][A-Z0-9./\-]{0,9})\s+(?P<verb>\w+)(?:\s+(?P<rest>.*))?\s*$"
)

# Pattern C: "DONE A1234 100sh @ 419.50" or "FILLED A1234 100sh @419.50"
EXECUTION_PATTERN = re.compile(
    r"^\s*(?:DONE|FILLED|EXECUTED)\s+A\s*(?P<alert_id>\d+)"
    r"(?:\s+(?P<shares>\d+(?:\.\d+)?)\s*(?:sh|shs|shares)?)?"
    r"(?:\s*@\s*(?P<price>\d+(?:\.\d+)?))?\s*$",
    re.IGNORECASE,
)


def _parse_amount(rest: str | None) -> tuple[float | None, str | None]:
    """Pull a dollar amount or share count out of the trailing text.
    Returns (dollars, shares) - either may be None.

    Heuristic: "100" or "$100" -> dollars. "100sh" or "100 shares" -> shares.
    """
    if not rest:
        return None, None
    rest = rest.strip()
    m = re.match(r"^\$?(\d+(?:\.\d+)?)\s*(sh|shs|shares)?\s*$", rest, re.IGNORECASE)
    if not m:
        return None, None
    n = float(m.group(1))
    if m.group(2):
        return None, n
    return n, None


def parse_decision(text: str) -> dict | None:
    """Parse a user's Telegram message into a structured decision.

    Returns None if the message doesn't look like a decision command.
    Returned dict keys:
        verb         — uppercase verb (BUY/SELL/PASS/etc), required
        alert_id     — int if user referenced an alert id, else None
        ticker       — uppercase ticker if no alert id provided, else None
        is_execution — True for DONE/FILLED messages
        shares       — float or None
        dollars      — float or None
        raw_text     — original text
    """
    if not text or not text.strip():
        return None

    # First check execution-confirmation pattern (DONE A1234 100sh @ 419.50)
    m = EXECUTION_PATTERN.match(text)
    if m:
        return {
            "verb": "DONE",
            "alert_id": int(m.group("alert_id")),
            "ticker": None,
            "is_execution": True,
            "shares": float(m.group("shares")) if m.group("shares") else None,
            "dollars": None,
            "price": float(m.group("price")) if m.group("price") else None,
            "raw_text": text,
        }

    # Then alert-id pattern (A1234 BUY 100)
    m = ALERT_ID_PATTERN.match(text)
    if m and m.group("verb").lower() in DECISION_VERBS:
        dollars, shares = _parse_amount(m.group("rest"))
        return {
            "verb": m.group("verb").upper(),
            "alert_id": int(m.group("alert_id")),
            "ticker": None,
            "is_execution": False,
            "shares": shares,
            "dollars": dollars,
            "price": None,
            "raw_text": text,
        }

    # Then ticker-only pattern (OIH BUY 100)
    m = TICKER_PATTERN.match(text)
    if m and m.group("verb").lower() in DECISION_VERBS:
        dollars, shares = _parse_amount(m.group("rest"))
        return {
            "verb": m.group("verb").upper(),
            "alert_id": None,
            "ticker": m.group("ticker").upper(),
            "is_execution": False,
            "shares": shares,
            "dollars": dollars,
            "price": None,
            "raw_text": text,
        }

    return None


def handle_decision(decision: dict) -> str:
    """Resolve a parsed decision: look up the alert, save user_action, return reply text."""
    try:
        import db_pg
    except ImportError:
        return "Decision noted but db layer unavailable: " + decision.get("raw_text", "")

    # Resolve which alert this decision is about
    alert = None
    alert_id = decision.get("alert_id")
    ticker = decision.get("ticker")
    if alert_id:
        alert = db_pg.find_alert_by_id(alert_id)
        if not alert:
            return f"Alert A{alert_id} not found in db. Decision not logged."
        ticker_resolved = alert["ticker"]
    elif ticker:
        alert = db_pg.find_recent_alert_for_ticker(ticker, hours=48)
        if alert:
            ticker_resolved = alert["ticker"]
            alert_id = alert["id"]
        else:
            ticker_resolved = ticker  # log decision without an alert link
    else:
        return "Could not resolve which trade you mean (no alert id, no ticker)."

    if decision["is_execution"]:
        # DONE A1234 - update existing user_action to executed=True. Find the most
        # recent un-executed user_action for this alert.
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id FROM user_actions
                    WHERE alert_id = %s AND executed = FALSE
                    ORDER BY decided_at DESC LIMIT 1
                    """,
                    (alert_id,),
                )
                row = cur.fetchone()
        if not row:
            return f"No un-executed action found for alert A{alert_id}."
        action_id = row[0]
        db_pg.update_user_action_executed(
            action_id,
            executed_action="EXECUTED",
            executed_shares=decision.get("shares"),
            executed_price=decision.get("price"),
        )
        return (
            f"Marked action #{action_id} on A{alert_id} {ticker_resolved} as EXECUTED"
            + (f" ({decision['shares']:.0f}sh @ ${decision['price']:.2f})" if decision.get("shares") and decision.get("price") else "")
            + "."
        )

    # Regular decision: save a new user_action row
    action_id = db_pg.save_user_action(
        ticker=ticker_resolved,
        decision=decision["verb"],
        alert_id=alert_id,
        executed=False,
        executed_dollars=decision.get("dollars"),
        executed_shares=decision.get("shares"),
        raw_telegram_text=decision["raw_text"],
    )
    suffix = f"A{alert_id}" if alert_id else "(no recent alert)"
    return (
        f"Logged decision #{action_id}: {decision['verb']} {ticker_resolved} {suffix}"
        + (f" ${decision['dollars']:.0f}" if decision.get("dollars") else "")
        + (f" {decision['shares']:.0f}sh" if decision.get("shares") else "")
        + ". Reply DONE A<id> sh @ price when filled."
    )


def _dispatch_message(token, chat_id, text):
    """Route one whitelisted text message through the handler chain. First handler to
    return non-None OWNS the message and replies. Each handler is individually guarded
    so one handler's bug can't block the others (every message is offered to all of
    them) — but a handler that RAISES is remembered, and if nothing ends up handling
    the message the error is surfaced as a reply instead of masquerading as a plain
    echo. The whole call is also wrapped by the caller (crash containment)."""
    errors = []

    def run(name, fn):
        try:
            return fn()
        except Exception as e:
            log.error(f"{name} handler failed: {e}", exc_info=True)
            errors.append(f"{name}: {e}")
            return None

    # (name, thunk) in priority order; imports inside the thunk so an import error is
    # caught too. Sentinel-gated handlers return None to decline.
    def _ss():   from tools.ss_roster import handle_telegram_text;        return handle_telegram_text(text)
    def _quad(): from tools.quad_manual import handle_quad_command;       return handle_quad_command(text)
    def _scr():  from tools.screener import handle_screen_command;        return handle_screen_command(text, chat_id)
    def _qc():   from tools.quad_confirm import handle_quad_confirm_reply; return handle_quad_confirm_reply(text)
    def _mv():   from tools.bucket_history import handle_moves_command;   return handle_moves_command(text)
    def _bl():   from tools.enrollment import handle_backlog_command;     return handle_backlog_command(text)

    for name, fn in (("ss_roster", _ss), ("quad", _quad), ("screen", _scr),
                     ("quad_confirm", _qc), ("moves", _mv), ("backlog", _bl)):
        reply = run(name, fn)
        if reply is not None:
            _send_message(token, chat_id, reply)
            return

    # Structured trade decision, else echo. If a handler errored above and nothing
    # claimed the message, surface the error rather than a benign echo.
    decision = parse_decision(text)
    if decision:
        reply = run("decision", lambda: handle_decision(decision))
        _send_message(token, chat_id, reply if reply is not None
                      else f"Decision parsing error: {errors[-1] if errors else 'unknown'}")
    elif errors:
        _send_message(token, chat_id, f"🛑 handler error: {errors[0]}")
    else:
        _send_message(token, chat_id, f"Got it: {text}")


def _write_listener_heartbeat():
    """Stamp bot_state so the doctor can detect a dead listener even while the process
    stays 'healthy'. Best-effort; never raises into the loop."""
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_state (key,value,updated_at) VALUES (%s,%s,NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                (LISTENER_HEARTBEAT_KEY, datetime.now(timezone.utc).isoformat()))
            c.commit()
    except Exception as e:
        log.warning(f"listener heartbeat write failed: {e}")


def _run_listener(token, allowed_chat_id):
    _delete_webhook(token)
    offset = _drain_pending_updates(token)
    last_heartbeat = 0.0

    log.info(f"Telegram listener started. Whitelisted chat_id: {allowed_chat_id}")

    while True:
        try:
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                _write_listener_heartbeat()
                last_heartbeat = now
            params = {"timeout": LONG_POLL_TIMEOUT}
            if offset is not None:
                params["offset"] = offset

            response = requests.get(
                API_BASE.format(token=token, method="getUpdates"),
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            if response.status_code == 401:
                log.error(
                    "Telegram 401 Unauthorized — bad TELEGRAM_BOT_TOKEN. Exiting listener thread."
                )
                return
            if response.status_code == 409:
                log.error(
                    "Telegram 409 Conflict — another getUpdates poller is active. Sleeping 30s."
                )
                time.sleep(CONFLICT_SLEEP)
                continue
            response.raise_for_status()

            updates = response.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat_id = message.get("chat", {}).get("id")
                text = message.get("text", "")

                if str(chat_id) != str(allowed_chat_id):
                    log.info(f"Dropped message from non-whitelisted chat_id={chat_id}.")
                    continue

                if not text:
                    log.info(f"Skipped non-text message from chat {chat_id}.")
                    continue

                log.info(f"Received from {chat_id}: {text!r}")
                # Crash containment: ANY failure handling one message logs a traceback,
                # replies an error to the chat, and the loop continues to the next
                # update. A single poisoned command can never kill the listener. The
                # offset was already advanced above, so a message that reliably throws
                # is acked (won't be re-fetched forever).
                try:
                    _dispatch_message(token, chat_id, text)
                except Exception as e:
                    log.error(f"dispatch failed for {text!r}: {e}", exc_info=True)
                    _send_message(token, chat_id, f"🛑 handler error: {e}")

        except Exception as e:
            log.error(f"Listener loop error: {e}. Sleeping {GENERAL_ERROR_SLEEP}s.")
            time.sleep(GENERAL_ERROR_SLEEP)


def _alert_listener_down(token, chat_id, reason, next_retry_s):
    """Loud death: alert both via the priority notifier (Pushover, if wired) and a
    direct Telegram sendMessage, so a dead listener is never silent."""
    msg = f"🚨 Telegram listener DOWN ({reason}). Restarting in {next_retry_s}s."
    try:
        from notifier import send_telegram as _notify
        _notify("Listener down", msg, priority=1)
    except Exception as e:
        log.error(f"listener-down notifier alert failed: {e}")
    try:
        _api_get(token, "sendMessage",
                 params={"chat_id": chat_id, "text": msg}, timeout=10)
    except Exception as e:
        log.error(f"listener-down direct alert failed: {e}")


def _listener_supervisor(token, chat_id):
    """Never let the process stay 'healthy' with a dead command interface. Run the
    listener; if it returns or crashes, log CRITICAL, alert, and restart with
    exponential backoff (reset after a healthy run)."""
    backoff = 5
    while True:
        started = time.time()
        try:
            _run_listener(token, chat_id)
            log.critical("telegram listener returned unexpectedly; restarting.")
            reason = "listener returned"
        except Exception as e:
            log.critical(f"telegram listener CRASHED: {e}", exc_info=True)
            reason = f"crash: {e}"
        # Reset backoff if it had been running healthily; otherwise escalate.
        backoff = 5 if (time.time() - started) > 120 else min(backoff * 2, 300)
        _alert_listener_down(token, chat_id, reason, backoff)
        time.sleep(backoff)


def start_telegram_listener():
    """Spawn the supervised Telegram listener as a daemon background thread. The
    supervisor restarts the listener on death so the command interface can't die
    silently while the process stays up."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; listener not started."
        )
        return None

    thread = threading.Thread(
        target=_listener_supervisor,
        args=(token, chat_id),
        name="telegram-listener-supervisor",
        daemon=True,
    )
    thread.start()
    log.info("Telegram listener (supervised) thread launched.")
    return thread
