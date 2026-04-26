"""
Hedgeye Bot — Main Entry Point
Runs the portal scraper and email parser concurrently in separate threads.
"""

import logging
import threading
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

log = logging.getLogger(__name__)


def run_scraper():
    from scraper import main as scraper_main
    log.info("Starting portal scraper thread...")
    scraper_main()


def run_email_parser():
    from email_parser import run_email_loop
    log.info("Starting email parser thread...")
    run_email_loop()


def check_env():
    """Verify all required environment variables are set."""
    required = [
        "HEDGEYE_EMAIL",
        "HEDGEYE_PASSWORD",
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

    scraper_thread = threading.Thread(target=run_scraper, daemon=True, name="scraper")
    email_thread   = threading.Thread(target=run_email_parser, daemon=True, name="email")

    scraper_thread.start()
    email_thread.start()

    log.info("Hedgeye bot running. Both threads active.")
    log.info("Portal scraper: every 15 minutes")
    log.info("Email parser:   every 15 minutes")

    # Keep main thread alive
    scraper_thread.join()
    email_thread.join()
