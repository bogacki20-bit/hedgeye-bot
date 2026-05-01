import os
import logging
import requests

log = logging.getLogger("notifier")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram(title, message, priority=1):
    """
    Send a Telegram notification. Returns True on success.

    The `priority` argument is accepted for backwards compatibility with the
    old Pushover signature but is ignored — Telegram has no priority levels.
    """
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; cannot send.")
        return False

    text = f"*{title}*\n\n{message}" if title else message

    try:
        response = requests.post(
            TELEGRAM_API.format(token=token),
            data={
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        response.raise_for_status()
        log.info(f"Telegram sent: {title}")
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


send_pushover = send_telegram
