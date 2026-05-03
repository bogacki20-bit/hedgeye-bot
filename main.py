"""
Hedgeye Bot — Main Entry Point.

Boot order:
  1. Verify required env vars are present.
  2. Verify Postgres is reachable and the schema is applied (fail fast).
  3. Send Telegram startup ping.
  4. Start Telegram listener (handles approve/reject replies).
  5. Run the email parser loop (forever).
"""

import logging
import sys
import os

from telegram_handler import start_telegram_listener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)


def check_env() -> None:
    required = [
        "ICLOUD_EMAIL",
        "ICLOUD_APP_PASSWORD",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DATABASE_URL",  # Postgres (Railway-internal hostname for the deployed bot)
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(f"Missing environment variables: {', '.join(missing)}")
        log.error("Create a .env file with these values, or set them in the Railway dashboard.")
        sys.exit(1)
    log.info("All required environment variables present.")


def check_postgres() -> None:
    """Fail fast if Postgres isn't reachable or the schema isn't applied."""
    try:
        import db_pg
        tables = db_pg.smoke_test()
    except Exception as e:
        log.error(f"Postgres connection failed: {e}")
        sys.exit(1)

    expected_tables = {
        "hedgeye_emails_raw",
        "imap_backfill_state",
        "hedgeye_risk_ranges",
        "mfr_snapshots",
        "alerts_fired",
    }
    missing = expected_tables - set(tables)
    if missing:
        log.error(f"Postgres connected but expected tables are missing: {sorted(missing)}")
        log.error("Run: railway run python apply_schema.py")
        sys.exit(1)

    log.info(f"Postgres connected. {len(tables)} tables in public schema.")


if __name__ == "__main__":
    check_env()
    check_postgres()

    from notifier import send_telegram
    send_telegram("Hedgeye Bot", "Bot started on Railway. Postgres + Telegram OK.")
    log.info("Startup ping sent.")

    from email_parser import run_email_loop
    start_telegram_listener()
    log.info("Hedgeye bot running — email parser → Postgres lake.")
    run_email_loop()
