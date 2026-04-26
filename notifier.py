"""
Push Notifier via Pushover
Sends alerts for trade signals and morning briefs.
"""

import os
import logging
import urllib.request
import urllib.parse

log = logging.getLogger(__name__)

PUSHOVER_TOKEN = os.environ["PUSHOVER_TOKEN"]
PUSHOVER_USER  = os.environ["PUSHOVER_USER"]
PUSHOVER_URL   = "https://api.pushover.net/1/messages.json"


def send_notification(message: str, title: str = "Hedgeye Bot"):
    """Send a push notification via Pushover."""
    payload = urllib.parse.urlencode({
        "token":   PUSHOVER_TOKEN,
        "user":    PUSHOVER_USER,
        "title":   title,
        "message": message[:1024],  # Pushover limit
    }).encode()

    try:
        req = urllib.request.Request(PUSHOVER_URL, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info(f"Pushover notification sent (status {resp.status}).")
    except Exception as e:
        log.error(f"Pushover send failed: {e}")
