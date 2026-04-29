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
import requests

log = logging.getLogger("telegram")

API_BASE = "https://api.telegram.org/bot{token}/{method}"
LONG_POLL_TIMEOUT = 30
HTTP_TIMEOUT = LONG_POLL_TIMEOUT + 5
GENERAL_ERROR_SLEEP = 5
CONFLICT_SLEEP = 30


def _api_get(token, method, params=None, timeout=HTTP_TIMEOUT):
    url = API_BASE.format(token=token, method=method)
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _send_message(token, chat_id, text):
    try:
        _api_get(
            token,
            "sendMessage",
            params={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        log.info(f"Sent reply to chat {chat_id}: {text!r}")
    except Exception as e:
        log.error(f"sendMessage failed: {e}")


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


def _run_listener(token, allowed_chat_id):
    _delete_webhook(token)
    offset = _drain_pending_updates(token)

    log.info(f"Telegram listener started. Whitelisted chat_id: {allowed_chat_id}")

    while True:
        try:
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
                _send_message(token, chat_id, f"Got it: {text}")

        except Exception as e:
            log.error(f"Listener loop error: {e}. Sleeping {GENERAL_ERROR_SLEEP}s.")
            time.sleep(GENERAL_ERROR_SLEEP)


def start_telegram_listener():
    """Spawn the Telegram listener as a daemon background thread."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; listener not started."
        )
        return None

    thread = threading.Thread(
        target=_run_listener,
        args=(token, chat_id),
        name="telegram-listener",
        daemon=True,
    )
    thread.start()
    log.info("Telegram listener thread launched.")
    return thread
