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
import threading

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

    # Risk Range parser runs in a daemon thread alongside the email loop.
    # Daemon = if it dies, main thread keeps going (the bot stays up).
    # Toggle off via PARSER_ENABLED=false on Railway if needed.
    if os.getenv("PARSER_ENABLED", "true").lower() in ("true", "1", "yes"):
        from parser_risk_range import run_parser_loop as run_risk_range_parser

        def _resilient_parser_loop():
            """Auto-restart wrapper. Pre-fix the daemon could die on an
            uncaught exception and the bot would keep running with RR silently
            dark for hours. Now: log the traceback, ping Telegram, sleep
            briefly, and restart the loop. Each crash bumps a counter so
            chronic failures are visible."""
            import time, traceback
            crash_count = 0
            while True:
                try:
                    run_risk_range_parser()
                except Exception as e:
                    crash_count += 1
                    tb = traceback.format_exc()
                    log.error(
                        "Risk Range parser crashed (#%d): %s\n%s",
                        crash_count, e, tb,
                    )
                    try:
                        from notifier import send_telegram
                        send_telegram(
                            "Hedgeye Bot",
                            (f"Risk Range parser crashed (#{crash_count}): "
                             f"{type(e).__name__}: {e}\nRestarting in 60s. "
                             f"Tail: {tb[-400:]}"),
                        )
                    except Exception as notify_err:
                        log.warning("crash notification failed: %s", notify_err)
                    time.sleep(60)

        threading.Thread(
            target=_resilient_parser_loop,
            daemon=True,
            name="risk_range_parser",
        ).start()
        log.info("Risk Range parser thread started (with crash auto-restart).")
    else:
        log.info("Risk Range parser disabled (PARSER_ENABLED=false).")

    # Live price monitor — polls yfinance, fires Telegram on range-edge events.
    # Same daemon pattern. Toggle via MONITOR_ENABLED=false on Railway if alerts get noisy.
    if os.getenv("MONITOR_ENABLED", "true").lower() in ("true", "1", "yes"):
        from price_monitor import run_monitor_loop
        threading.Thread(
            target=run_monitor_loop,
            daemon=True,
            name="price_monitor",
        ).start()
        log.info("Price monitor thread started.")
    else:
        log.info("Price monitor disabled (MONITOR_ENABLED=false).")

    # MFR watchlist sync — once per UTC day, refresh every ticker in the
    # operator's MFR account (canonical fan-out source). Catches new tickers
    # added through the MFR UI without any code change. Toggle off via
    # MFR_WATCHLIST_SYNC=false. Tracks last-sync UTC date in bot_state so
    # restarts within the same day don't re-run.
    if os.getenv("MFR_WATCHLIST_SYNC", "true").lower() in ("true", "1", "yes"):
        def _mfr_watchlist_loop():
            """Sleep until next UTC midnight + 2 min, then refresh. On crash,
            log + ping Telegram + retry after 1h (the daily cadence is loose
            enough that an occasional skipped day is fine)."""
            import time, traceback
            from datetime import datetime, timezone, timedelta
            import db_pg, mfr_client
            while True:
                try:
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    last_sync = None
                    try:
                        with db_pg.get_conn() as conn, conn.cursor() as cur:
                            cur.execute("SELECT value FROM bot_state "
                                        "WHERE key = 'mfr_watchlist_last_sync_utc'")
                            row = cur.fetchone()
                            if row: last_sync = row[0]
                    except Exception as e:
                        log.debug("bot_state read failed (continuing): %s", e)

                    if last_sync != today:
                        summary = mfr_client.refresh_watchlist()
                        try:
                            with db_pg.get_conn() as conn, conn.cursor() as cur:
                                cur.execute(
                                    """INSERT INTO bot_state (key, value, updated_at)
                                       VALUES ('mfr_watchlist_last_sync_utc', %s, NOW())
                                       ON CONFLICT (key) DO UPDATE
                                         SET value = EXCLUDED.value, updated_at = NOW()""",
                                    (today,),
                                )
                                conn.commit()
                        except Exception as e:
                            log.warning("bot_state write for mfr_watchlist failed: %s", e)
                        log.info("mfr_watchlist_sync: done — %s", summary)
                    else:
                        log.debug("mfr_watchlist_sync: already synced today (%s)", today)

                    # Sleep to next UTC midnight + 2 minutes (small offset so
                    # multiple instances on the same day don't dogpile at 00:00).
                    now = datetime.now(timezone.utc)
                    nxt = (now + timedelta(days=1)).replace(hour=0, minute=2,
                                                            second=0, microsecond=0)
                    secs = max(60, int((nxt - now).total_seconds()))
                    time.sleep(secs)
                except Exception as e:
                    tb = traceback.format_exc()
                    log.error("mfr_watchlist_sync crashed: %s\n%s", e, tb)
                    try:
                        from notifier import send_telegram
                        send_telegram(
                            "Hedgeye Bot",
                            f"MFR watchlist sync crashed: {type(e).__name__}: {e}\n"
                            f"Retrying in 1h. Tail: {tb[-300:]}",
                        )
                    except Exception:
                        pass
                    time.sleep(3600)

        threading.Thread(target=_mfr_watchlist_loop, daemon=True,
                         name="mfr_watchlist_sync").start()
        log.info("MFR watchlist sync thread started.")
    else:
        log.info("MFR watchlist sync disabled (MFR_WATCHLIST_SYNC=false).")

    log.info("Hedgeye bot running — email parser → Postgres lake.")
    run_email_loop()
