"""
Hedgeye Bot — Main Entry Point
"""

import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

log = logging.getLogger(__name__)


def check_env():
    required = [
        "ICLOUD_EMAIL",
        "ICLOUD_APP_PASSWORD",
        "ANTHROPIC_API_KEY",
        "PUSHOVER_TOKEN",
        "PUSHOVER_USER",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing environment variables: {', '.join(missing)}")
        log.error("Create a .env file with these values. See .env.example")
        sys.exit(1)
    log.info("All environment variables present.")


if __name__ == "__main__":
    check_env()

    from notifier import send_notification
    send_notification("Hedgeye Bot Online", title="Hedgeye Bot")
    log.info("Startup notification sent.")

    from email_parser import run_email_loop
    log.info("Hedgeye bot running — email parser only.")
    run_email_loop()
