import os
import logging
import requests

log = logging.getLogger("notifier")

def send_pushover(title, message, priority=1):
    """Send a Pushover notification. Returns True on success."""
    try:
        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": os.environ["PUSHOVER_TOKEN"],
                "user": os.environ["PUSHOVER_USER"],
                "title": title,
                "message": message,
                "priority": priority,
            },
            timeout=10
        )
        response.raise_for_status()
        log.info(f"Pushover sent: {title}")
        return True
    except Exception as e:
        log.error(f"Pushover failed: {e}")
        return False
