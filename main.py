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

    # Boot marker -> bot_state so deploys are verifiable from the DB: which commit
    # is live + when it booted. RAILWAY_GIT_COMMIT_SHA is set by Railway for GitHub
    # deploys; fall back to a local git call, else 'unknown'.
    import subprocess
    from datetime import datetime, timezone
    _sha = os.environ.get("RAILWAY_GIT_COMMIT_SHA")
    if not _sha:
        try:
            _sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                  text=True, timeout=5).stdout.strip() or "unknown"
        except Exception:
            _sha = "unknown"
    _boot_at = datetime.now(timezone.utc).isoformat()
    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                for _k, _v in (("bot_git_sha", _sha), ("bot_boot_at", _boot_at)):
                    cur.execute(
                        """INSERT INTO bot_state (key, value, updated_at)
                           VALUES (%s, %s, NOW())
                           ON CONFLICT (key) DO UPDATE
                             SET value = EXCLUDED.value, updated_at = NOW()""",
                        (_k, _v),
                    )
            conn.commit()
        log.info("boot marker written: sha=%s at=%s", _sha[:12], _boot_at)
    except Exception as e:
        log.warning("could not write boot marker to bot_state: %s", e)

    from notifier import send_telegram
    send_telegram("Hedgeye Bot", f"Bot started on Railway (sha {_sha[:12]}). Postgres + Telegram OK.")
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

    # Friday SS-roster anchor prompt + stale re-ping (PROMPT ONLY — writes no roster).
    # Toggle off via SS_ANCHOR_PROMPT=false.
    if os.getenv("SS_ANCHOR_PROMPT", "true").lower() in ("true", "1", "yes"):
        def _ss_anchor_prompt_loop():
            import time
            from tools.ss_roster import maybe_send_anchor_prompt
            while True:
                try:
                    status = maybe_send_anchor_prompt()
                    if status.startswith("sent"):
                        log.info("ss_anchor_prompt: %s", status)
                except Exception as e:
                    log.error("ss_anchor_prompt loop error: %s", e)
                time.sleep(1800)  # check every 30 min
        threading.Thread(target=_ss_anchor_prompt_loop, daemon=True,
                         name="ss-anchor-prompt").start()
        log.info("SS anchor prompt thread started.")
    else:
        log.info("SS anchor prompt disabled (SS_ANCHOR_PROMPT=false).")

    # MFR enrollment helpers (READ-ONLY — tell me what to activate in MFR; no write token):
    #   nightly to-add (go-forward adds, ~8pm ET) + weekly backlog sweep (full catch-up,
    #   Sun ~7pm ET, once/ISO-week, persisted-flag). Source-agnostic (enrollment_sources.REGISTRY).
    #   Toggles: MFR_TOADD_ENABLED / MFR_BACKLOG_ENABLED. (On-demand "MFR BACKLOG" via Telegram.)
    _toadd_on = os.getenv("MFR_TOADD_ENABLED", "true").lower() in ("true", "1", "yes")
    _backlog_on = os.getenv("MFR_BACKLOG_ENABLED", "true").lower() in ("true", "1", "yes")
    if _toadd_on or _backlog_on:
        def _mfr_enroll_loop():
            import time
            from zoneinfo import ZoneInfo
            from datetime import datetime as _dt
            while True:
                try:
                    now_et = _dt.now(ZoneInfo("America/New_York"))
                    if _toadd_on and now_et.hour >= 20:                       # nightly, 8pm ET
                        from tools.enrollment import run_nightly
                        st = run_nightly()
                        if st.startswith("sent"):
                            log.info("mfr_toadd: %s", st)
                    if _backlog_on and now_et.weekday() == 6 and now_et.hour >= 19:  # weekly, Sun 7pm ET
                        from tools.enrollment import run_weekly_backlog
                        st = run_weekly_backlog()
                        if st.startswith("sent"):
                            log.info("mfr_backlog: %s", st)
                except Exception as e:
                    log.error("mfr_enroll loop error: %s", e)
                time.sleep(1800)  # check every 30 min
        threading.Thread(target=_mfr_enroll_loop, daemon=True, name="mfr-enroll").start()
        log.info("MFR enroll thread started (to-add=%s, backlog=%s).", _toadd_on, _backlog_on)
    else:
        log.info("MFR enroll disabled (MFR_TOADD_ENABLED & MFR_BACKLOG_ENABLED both false).")

    # Quad early-warning (Stage 1, READ-ONLY + Telegram only — never writes the
    # quad). The daily Macro Show / Early Look tone front-runs the official
    # monthly/quarterly flip by ~a week; when the thematic quad runs different
    # from the official stored quad for >= 3 consecutive note-days, send ONE
    # heads-up per divergence episode. The official flip itself is proposed
    # inline on a Quads/GIP deck email and applied via the QUAD: bridge.
    # Toggle: QUAD_EARLYWARN_ENABLED.
    if os.getenv("QUAD_EARLYWARN_ENABLED", "true").lower() in ("true", "1", "yes"):
        def _quad_earlywarn_loop():
            import time
            while True:
                try:
                    from tools.quad_detector import run_early_warning
                    st = run_early_warning()
                    if st.startswith("sent"):
                        log.info("quad_earlywarn: %s", st)
                except Exception as e:
                    log.error("quad_earlywarn loop error: %s", e)
                time.sleep(1800)  # check every 30 min; internal once/day + per-episode throttle
        threading.Thread(target=_quad_earlywarn_loop, daemon=True, name="quad-earlywarn").start()
        log.info("Quad early-warning thread started.")
    else:
        log.info("Quad early-warning disabled (QUAD_EARLYWARN_ENABLED=false).")

    # Morning quad-staleness ping (~6:00am ET). If the quad hasn't been confirmed in
    # QUAD_CONFIRM_MAX_AGE_DAYS (default 1), Telegram a reminder — reply OK to
    # confirm (stamps last-confirmed; does NOT change the quad) or QUAD: to change.
    # Never infers/auto-sets the quad. Toggle: QUAD_CONFIRM_ENABLED.
    if os.getenv("QUAD_CONFIRM_ENABLED", "true").lower() in ("true", "1", "yes"):
        def _quad_confirm_loop():
            import time
            from zoneinfo import ZoneInfo
            from datetime import datetime as _dt
            while True:
                try:
                    if _dt.now(ZoneInfo("America/New_York")).hour >= 6:   # ~6am ET onward
                        from tools.quad_confirm import run_morning_ping
                        st = run_morning_ping()
                        if st.startswith("sent"):
                            log.info("quad_confirm ping: %s", st)
                except Exception as e:
                    log.error("quad_confirm loop error: %s", e)
                time.sleep(1800)  # check every 30 min; internal once/day throttle
        threading.Thread(target=_quad_confirm_loop, daemon=True, name="quad-confirm").start()
        log.info("Quad morning-ping thread started.")
    else:
        log.info("Quad morning-ping disabled (QUAD_CONFIRM_ENABLED=false).")

    # HTTP API — serves /api/scrape_ingest (+ /api/tape_report compat alias) so
    # scheduled scrape SKILLs (running in a sandbox with no DB access) can route
    # captures into Postgres over HTTP. Same daemon pattern. Toggle via
    # API_ENABLED=false. Requires a public domain on the Railway service +
    # SCRAPE_INGEST_SECRET set.
    if os.getenv("API_ENABLED", "true").lower() in ("true", "1", "yes"):
        try:
            from api import run_api_server

            def _resilient_api_loop():
                import time, traceback
                while True:
                    try:
                        run_api_server()
                    except Exception as e:
                        tb = traceback.format_exc()
                        log.error("API server crashed: %s\n%s", e, tb)
                        try:
                            from notifier import send_telegram
                            send_telegram(
                                "Hedgeye Bot",
                                f"API server crashed: {type(e).__name__}: {e}\n"
                                f"Restarting in 30s. Tail: {tb[-300:]}",
                            )
                        except Exception:
                            pass
                        time.sleep(30)

            threading.Thread(
                target=_resilient_api_loop, daemon=True, name="api_server"
            ).start()
            log.info("API server thread started (/api/scrape_ingest, /api/tape_report).")
        except Exception as e:
            log.error("Failed to start API server thread: %s", e)
    else:
        log.info("API server disabled (API_ENABLED=false).")

    log.info("Hedgeye bot running — email parser → Postgres lake.")
    run_email_loop()
