"""
iCloud Email Parser — Postgres email-lake edition.

Connects to iCloud IMAP, watches for Hedgeye emails, saves the raw email
verbatim into hedgeye_emails_raw (the email lake), classifies the content
for immediate Telegram notification, and (for trade signals) hands off to
the recommender.

Dedup is now via hedgeye_emails_raw.message_id (RFC 5322 Message-ID) with
ON CONFLICT DO NOTHING — the SQLite processed_emails table is no longer used.

The classifier output is intentionally NOT persisted into typed tables here.
That's the job of dedicated parsers running over the lake (Risk Range parser,
ETF Pro parser, Quad Nowcast parser, etc.). This module's job is:
  - get the email
  - put the bytes in the lake
  - fire the immediate notification
  - call the recommender for trade signals (recommender still writes to SQLite
    trade_recommendations for now; that migration is queued separately)

Environment:
  BACKFILL_MODE = off | silent | notify
    off    (default) — normal operation, Telegram for every new email
    silent           — process and store everything, NO Telegram
    notify           — process AND Telegram (use sparingly; can flood)
  EMAIL_CHECK_INTERVAL — poll interval in seconds (default 900 / 15 min)
"""

import os
import imaplib
import email
import time
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from classifier import classify_and_extract
from notifier import send_pushover
import db_pg

log = logging.getLogger(__name__)

ICLOUD_EMAIL    = os.environ["ICLOUD_EMAIL"]
ICLOUD_PASSWORD = os.environ["ICLOUD_APP_PASSWORD"]
IMAP_HOST       = "imap.mail.me.com"
IMAP_PORT       = 993
CHECK_INTERVAL  = int(os.getenv("EMAIL_CHECK_INTERVAL", "900"))

LOOKBACK_DAYS   = int(os.getenv("EMAIL_LOOKBACK_DAYS", "90"))
BACKFILL_MODE   = os.environ.get("BACKFILL_MODE", "off").lower()

# Cap the number of full-body fetches per cycle to avoid hammering iCloud IMAP
# with 1500+ rapid-fire fetches when the lake is fresh. Header peeks (cheap)
# are not capped — only full RFC822 fetches that actually pull the body.
# Backfill of historical emails clears at MAX_FULL_FETCHES * 4 cycles/hour,
# so 100/cycle = 400/hour means a 4-year archive backfills in days, a 90-day
# lookback in hours.
MAX_FULL_FETCHES_PER_CYCLE = int(os.getenv("MAX_FULL_FETCHES_PER_CYCLE", "100"))

# Substring keywords for the IMAP FROM search and the post-fetch sender check.
# IMAP FROM is substring-match, so "hedgeye.com" catches every user@hedgeye.com.
HEDGEYE_KEYWORDS = ["hedgeye.com", "tier1alpha"]


# ───────────────── Email body parsing ─────────────────

class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)

    def get_text(self):
        return " ".join(self.text_parts)


def decode_mime_header(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


def extract_body(msg) -> tuple[str, str]:
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct   = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                continue
            if ct == "text/plain" and not plain:
                plain = payload
            elif ct == "text/html" and not html:
                html = payload
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            payload = ""
        if msg.get_content_type() == "text/html":
            html = payload
        else:
            plain = payload
    return plain, html


def html_to_text(html: str) -> str:
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    text = extractor.get_text()
    return re.sub(r"\s{3,}", "\n\n", text).strip()


def is_hedgeye_sender(from_addr: str) -> bool:
    from_lower = (from_addr or "").lower()
    return any(kw in from_lower for kw in HEDGEYE_KEYWORDS)


def detect_teaser(html: str, text: str) -> tuple[str, str | None]:
    """
    Heuristic: is this a teaser email (with a "click here to read full report"
    link) or does it contain the full content?

    Returns (content_status, full_report_url):
      content_status: 'complete' | 'teaser' | 'unknown'
      full_report_url: the URL of the linked full report, if detected
    """
    if not html and not text:
        return "unknown", None
    body = (html or "") + " " + (text or "")
    body_lower = body.lower()

    # Common teaser phrases
    teaser_phrases = [
        "click here to read",
        "read the full",
        "view the full",
        "continue reading",
        "read more",
    ]
    is_teaser = any(p in body_lower for p in teaser_phrases)

    # Try to extract a hedgeye.com portal URL from anchor hrefs in the HTML
    full_url = None
    if html:
        m = re.search(
            r'<a[^>]+href=["\'](https?://[^"\']*hedgeye[^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if m:
            full_url = m.group(1)

    if is_teaser and full_url:
        return "teaser", full_url
    if is_teaser:
        return "teaser", None
    # If we have substantial body content (>1000 chars after strip) and no
    # teaser markers, treat as complete.
    if len(body.strip()) > 1000:
        return "complete", None
    return "unknown", full_url


def parse_email_message(raw_bytes: bytes, uid: str) -> dict | None:
    """Parse raw email bytes into a structured dict for the lake save.

    Returns None if the email should be dropped (non-Hedgeye sender, body too
    short, or unparseable).
    """
    try:
        msg = email.message_from_bytes(raw_bytes)

        from_addr = decode_mime_header(msg.get("From", ""))
        if not is_hedgeye_sender(from_addr):
            log.warning(f"  [{uid}] dropped — sender {from_addr!r} not a Hedgeye keyword match")
            return None

        # Message-ID is the RFC 5322 globally-unique id. Strip surrounding < >.
        raw_msg_id = decode_mime_header(msg.get("Message-ID", "")).strip().strip("<>")
        message_id = raw_msg_id if raw_msg_id else f"imap_uid_{uid}"

        subject  = decode_mime_header(msg.get("Subject", ""))
        date_str = msg.get("Date", "")
        try:
            received_at = parsedate_to_datetime(date_str)
            if received_at is None:
                received_at = datetime.now(timezone.utc)
        except Exception:
            received_at = datetime.now(timezone.utc)

        plain, html = extract_body(msg)
        body = plain.strip() if plain.strip() else html_to_text(html)

        if len(body) < 20:
            log.warning(f"  [{uid}] dropped — body too short ({len(body)} chars), subject={subject!r}")
            return None

        content_status, full_url = detect_teaser(html, plain)

        return {
            "message_id":      message_id,
            "imap_uid":        uid,
            "sender":          from_addr,
            "subject":         subject,
            "received_at":     received_at,
            "html_body":       html or None,
            "text_body":       plain or None,
            "full_report_url": full_url,
            "content_status":  content_status,
            "raw_size_bytes":  len(raw_bytes),
            # Convenience fields used by classifier/recommender (in-memory only)
            "_body_for_classifier": body[:4000],
        }

    except Exception as e:
        log.error(f"  [{uid}] parse error: {e}")
        return None


# ───────────────── IMAP fetch ─────────────────

def connect_imap() -> imaplib.IMAP4_SSL:
    """Connect to iCloud IMAP with a hard socket timeout set POST-handshake.

    History (2026-05-03):
      - No timeout: imaplib blocks indefinitely on stalled iCloud reads.
      - IMAP4_SSL(timeout=60) ctor kwarg: still hung silently — appears not to
        propagate properly to the SSL-wrapped socket on the imaplib read path.
      - socket.setdefaulttimeout(60) globally: corrupts SSL state mid-handshake
        (BAD_LENGTH errors) — DO NOT use; SSL frames straddle timeout boundaries.
      - threading.Timer that closes the socket: works for hangs but races with
        in-flight reads, returning empty data without raising (silent drops).
      - This approach: do the TLS handshake at default blocking timeout, then
        AFTER login+select succeeds, set a recv timeout on the established
        socket. Same thread, no race, SSL state is already settled so framing
        is stable. Stalled fetches surface as socket.timeout (an OSError),
        which the existing try/except catches and reconnects.
    """
    log.info("Connecting to iCloud IMAP...")
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(ICLOUD_EMAIL, ICLOUD_PASSWORD)
    conn.select("INBOX")
    # Now that handshake + auth are done, install a recv timeout on the live
    # SSL socket. Subsequent fetch/search/noop will respect this.
    try:
        conn.sock.settimeout(60)
        log.info("IMAP connected. Recv timeout set to 60s on established socket.")
    except Exception as e:
        log.warning(f"Could not set socket timeout post-handshake: {e}")
    return conn


def check_email(conn: imaplib.IMAP4_SSL) -> int:
    """One polling cycle. Returns the number of newly-saved emails this cycle."""
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
    candidate_uids: set[bytes] = set()

    for keyword in HEDGEYE_KEYWORDS:
        try:
            _, data = conn.search(None, f'(SINCE {since} FROM "{keyword}")')
            uids = data[0].split() if data[0] else []
            candidate_uids.update(uids)
        except imaplib.IMAP4.error as e:
            log.error(f"IMAP search error (FROM {keyword!r}): {e}")

    log.info(f"Email check: {len(candidate_uids)} candidate(s) since {since} "
             f"(BACKFILL_MODE={BACKFILL_MODE})")

    new_count = 0
    full_fetches_done = 0

    for uid in sorted(candidate_uids):
        uid_str = uid.decode()

        # Cheap header peek including Message-ID, so we can dedup BEFORE the
        # expensive full-body fetch. Header peeks are NOT capped per cycle —
        # only full RFC822 fetches are. This way we still process the entire
        # candidate list each cycle, just bounded on how much body data we pull.
        from_addr, subject, msg_id = "", "", ""
        try:
            log.info(f"  [{uid_str}] header peek...")  # heartbeat
            _, hdr_data = conn.fetch(uid, "(BODY[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])")
            hdr_bytes = hdr_data[0][1] if hdr_data and isinstance(hdr_data[0], tuple) else b""
            hdr_msg   = email.message_from_bytes(hdr_bytes)
            from_addr = decode_mime_header(hdr_msg.get("From", ""))
            subject   = decode_mime_header(hdr_msg.get("Subject", "(no subject)"))
            msg_id    = decode_mime_header(hdr_msg.get("Message-ID", "")).strip().strip("<>")
        except Exception as e:
            log.warning(f"  [{uid_str}] header peek failed ({e}) — bubbling up to reconnect")
            # Connection-level error — let it propagate so run_email_loop reconnects.
            raise

        # Sender filter (cheap, before any further work)
        if not is_hedgeye_sender(from_addr):
            log.info(f"  [{uid_str}] skip — non-Hedgeye sender {from_addr!r}")
            continue

        # Lake dedup BEFORE the expensive full fetch
        if msg_id and db_pg.is_email_seen(msg_id):
            log.info(f"  [{uid_str}] skip (already in lake) | subject={subject[:60]!r}")
            continue

        # Cap full-body fetches per cycle so we don't overload iCloud on a fresh lake
        if full_fetches_done >= MAX_FULL_FETCHES_PER_CYCLE:
            log.info(f"  [{uid_str}] deferring full fetch (cycle cap of "
                     f"{MAX_FULL_FETCHES_PER_CYCLE} reached)")
            continue

        log.info(f"  [{uid_str}] NEW | from={from_addr!r} | subject={subject[:60]!r}")

        # Fetch the full message and insert into the lake.
        # NOTE: Use BODY.PEEK[] not RFC822 — iCloud IMAP silently rejects the
        # legacy RFC822 data item (returns "UID ()" with zero attributes). The
        # modern BODY.PEEK[] spec returns the full message body and is honored
        # by iCloud (and is non-destructive — doesn't set the \Seen flag, so
        # the user's mail client doesn't show these emails as read by the bot).
        # Diagnosed 2026-05-03 via the explicit empty_raw_bytes log line.
        try:
            log.info(f"  [{uid_str}] full body fetch...")  # heartbeat
            _, raw = conn.fetch(uid, "(BODY.PEEK[])")
            full_fetches_done += 1

            if not raw or not raw[0]:
                log.warning(f"  [{uid_str}] empty fetch result (no raw[0])")
                continue
            raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else None
            if not raw_bytes:
                # Was previously a silent continue — now logged so this drop path
                # is visible. raw[0] type tells us what iCloud returned.
                log.warning(f"  [{uid_str}] empty raw_bytes — raw[0] type was "
                            f"{type(raw[0]).__name__}, repr={raw[0]!r}"[:200])
                continue

            parsed = parse_email_message(raw_bytes, uid_str)
            if not parsed:
                continue  # logged already

            inserted = db_pg.save_raw_email(
                message_id      = parsed["message_id"],
                sender          = parsed["sender"],
                subject         = parsed["subject"],
                received_at     = parsed["received_at"],
                html_body       = parsed["html_body"],
                text_body       = parsed["text_body"],
                imap_uid        = parsed["imap_uid"],
                full_report_url = parsed["full_report_url"],
                content_status  = parsed["content_status"],
                raw_size_bytes  = parsed["raw_size_bytes"],
            )

            if not inserted:
                log.info(f"  [{uid_str}] skip (already in lake — Message-ID race) "
                         f"| subject={subject[:70]!r}")
                continue

            log.info(f"  [{uid_str}] saved to lake | "
                     f"subject={subject[:70]!r} | status={parsed['content_status']}")
            new_count += 1
            _process_new_email(parsed)

        except (imaplib.IMAP4.abort, OSError) as e:
            # Connection-level error — let it propagate so run_email_loop reconnects.
            log.error(f"  [{uid_str}] connection error: {e} — reconnecting")
            raise
        except Exception as e:
            log.error(f"  [{uid_str}] processing error: {e}")

    if BACKFILL_MODE in ("silent", "notify") and new_count > 0:
        log.info(f"BACKFILL CYCLE COMPLETE — {new_count} new email(s) (mode={BACKFILL_MODE})")

    return new_count


def _process_new_email(parsed: dict) -> None:
    """For a freshly-saved email, run classifier + notifier + recommender.

    Classifier output is used in-memory for the immediate alert path. It is
    NOT persisted into typed tables here — that's the job of dedicated parsers
    that walk the lake separately (Risk Range parser, etc.).
    """
    notify = BACKFILL_MODE != "silent"

    item = {
        "id":        parsed["message_id"],   # used downstream as signal_item_id
        "title":     parsed["subject"],
        "subject":   parsed["subject"],
        "body":      parsed["_body_for_classifier"],
        "from":      parsed["sender"],
        "timestamp": parsed["received_at"].isoformat(),
        "source":    "email",
    }

    if notify:
        send_pushover(item["subject"] or "Hedgeye email", item["body"])

    try:
        item = classify_and_extract(item)
    except Exception as e:
        log.error(f"  classifier error on {parsed['message_id']}: {e}")
        return

    if item.get("classified_type") == "trade_signal" and item.get("ticker"):
        # Sized recommendation via decision_engine (slice 0c). For every
        # trade-signal email, call the Claude-backed decision_engine with
        # the classifier-tagged conviction and the multi-source context
        # (Hedgeye Quad + Risk Range + SpotGamma + MFR + Yahoo + corpus
        # FTS). decision_engine returns a structured decision dict — we
        # format it as a Telegram message and ping the user for approval.
        #
        # Non-fatal: any failure here logs a warning and falls through to
        # the universal ticker-inventory + lockstep refresh block below.
        # decision_engine.decide() returns None on missing API key or
        # API error, so we just check truthiness.
        try:
            import decision_engine
            ticker = item.get("ticker") or ""
            decision = decision_engine.decide(
                ticker.upper(),
                signal_origin="rta",
                signal_conviction=item.get("conviction"),
            )
            if decision:
                # Build a Telegram-friendly message body. Same shape as
                # price_monitor's alert text so the user sees a uniform
                # format whether the trigger was an email or a range edge.
                bps        = decision.get("bps")
                dollars    = decision.get("recommended_dollars")
                conf       = decision.get("confidence")
                reasoning  = decision.get("reasoning") or ""
                evidence   = decision.get("evidence") or []
                action     = decision.get("action") or "WATCH"
                conviction_tier = decision.get("conviction") or item.get("conviction", "?")

                size_str = (
                    f"${dollars:.0f} ({bps} bps)" if dollars and bps
                    else "no size"
                )
                title = f"Hedgeye: {action} {ticker.upper()} — {conviction_tier}"
                lines = [
                    f"→ {action} {ticker.upper()}  size {size_str}",
                    f"confidence: {conf:.0%}" if isinstance(conf, (int, float)) else "",
                    "",
                    reasoning[:400],
                ]
                if evidence:
                    lines.append("")
                    lines.append("evidence:")
                    for e in evidence[:5]:
                        lines.append(f"  • {str(e)[:120]}")
                msg = "\n".join(L for L in lines if L is not None)

                try:
                    from notifier import send_telegram
                    send_telegram(title, msg[:1024])
                    log.info(
                        f"  decision_engine fired: action={action} bps={bps} "
                        f"dollars={dollars} conviction={conviction_tier}"
                    )
                except Exception as te:
                    log.warning(f"  decision_engine telegram send failed: {te}")
            else:
                log.info(
                    f"  trade_signal detected: {item.get('direction', '?')} "
                    f"{item.get('ticker')} — decision_engine returned None "
                    f"(check ANTHROPIC_API_KEY)"
                )
        except Exception as e:
            log.warning(f"  decision_engine call failed: {e}")
            log.info(
                f"  trade_signal detected: {item.get('direction', '?')} "
                f"{item.get('ticker')} (conviction={item.get('conviction', '?')!r}) "
                f"— decision engine errored, falling through"
            )

    # ───────── Universal ticker inventory + MFR refresh ─────────
    # Every Hedgeye email — RTA, Risk Range, Signal Strength, ETF Pro,
    # Investing Ideas, Capital Allocation Pro, Financials/Retail Sector Pro,
    # Macro Show, Early Look — flows through here. Pull tickers out of the
    # classifier output and fire ticker_inventory.note_tickers + MFR refresh
    # so EVERY product's ticker mentions populate the inventory and keep MFR
    # snapshots current.
    try:
        tickers_in_msg = []
        positions_by_ticker: dict = {}
        for t in (item.get("tickers") or []):
            sym = (t.get("ticker") or "").strip().upper()
            if not sym:
                continue
            tickers_in_msg.append(sym)
            direction = (t.get("direction") or "").strip().lower()
            if direction in ("long", "buy", "bullish"):
                positions_by_ticker[sym] = "long"
            elif direction in ("short", "sell", "bearish"):
                positions_by_ticker[sym] = "short"
            elif direction in ("close", "remove", "exit"):
                positions_by_ticker[sym] = "closed"

        # If classifier dropped tickers but legacy item.ticker is set, include it
        legacy = (item.get("ticker") or "").strip().upper()
        if legacy and legacy not in tickers_in_msg:
            tickers_in_msg.append(legacy)
            legacy_dir = (item.get("direction") or "").strip().lower()
            if legacy_dir in ("long", "buy"):
                positions_by_ticker.setdefault(legacy, "long")
            elif legacy_dir in ("short", "sell"):
                positions_by_ticker.setdefault(legacy, "short")

        # Source detection runs unconditionally — typed parsers downstream key
        # off `source` and must fire even when the classifier returned no
        # tickers (Signal Strength bodies are mostly image-only; the delta
        # text "Added: X / Removed: Y" often doesn't make it into item.tickers).
        # Before, this block was gated behind `if tickers_in_msg:` and the
        # typed-parser dispatch sat inside it, which silently swallowed
        # Signal Strength / Portfolio Solutions / Investing Ideas / ETF Pro
        # emails whenever the classifier produced empty tickers.
        subject_lower = (item.get("subject") or "").lower()
        ctype = (item.get("classified_type") or "").lower()

        # Tier-1 daily-signal products. The RTA matcher uses a word-
        # boundary regex so "RTA:" / "RTA -" / "Daily RTA" all match
        # without false-positive on things like "EXTRACT" or "PORTAL".
        if "real-time alert" in subject_lower or re.search(r"\brta\b", subject_lower):
            source = "rta"
        elif "risk range" in subject_lower:
            source = "risk_range"

        # Sector / specialty Pro products (active subscriptions per
        # data/snapshots/hedgeye/2026-05-07/subscriptions_inventory.md)
        elif "capital allocation" in subject_lower:
            source = "capital_allocation_pro"
        elif "financial" in subject_lower and "sector" in subject_lower:
            source = "financials_sector_pro"
        elif "retail" in subject_lower and ("sector" in subject_lower or "pro" in subject_lower):
            source = "retail_pro"
        elif "reits" in subject_lower:
            source = "reits_pro"
        elif "hedgai" in subject_lower:
            source = "hedgai_signals"
        elif "bitcoin trend tracker" in subject_lower:
            source = "bitcoin_trend_tracker"
        elif "market situation report" in subject_lower:
            source = "market_situation_report"
        elif "pro risk manager" in subject_lower or "prm:" in subject_lower:
            # Branded Pro Risk Manager content not already caught by the
            # bundled-product matchers above.
            source = "pro_risk_manager"

        # Other Hedgeye content streams
        elif "signal strength" in subject_lower:
            source = "signal_strength"
        elif "etf pro" in subject_lower:
            source = "etf_pro"
        elif ("investing idea" in subject_lower
              or "top stock picks" in subject_lower
              or "top 21 long ideas" in subject_lower):
            # 2026-05-29: classifier missed every Top-21 Leaderboard email
            # since 2026-05-12 because the subject only matched
            # `"investing idea"`. Real subject is
            # "Top Stock Picks | The Leaderboard: Top 21 Long Ideas".
            # CASY was on every daily II leaderboard but the typed table
            # stayed stale at 2026-05-12 — drove the false-COVER bug.
            source = "investing_ideas"
        elif "macro show" in subject_lower:
            source = "macro_show"
        elif "early look" in subject_lower:
            source = "early_look"

        # Fallbacks driven by the classifier's structural categorization
        elif ctype == "sector_research":
            source = "sector_pro"
        elif ctype == "trade_signal":
            source = "trade_signal"
        else:
            source = "other"

        quad = item.get("macro_regime")  # classifier emits "Quad 1/2/3/4" or null

        # ticker_inventory + lockstep MFR/SpotGamma/Yahoo refresh only run
        # when the classifier actually produced ticker mentions — they need
        # symbols to operate on.
        if tickers_in_msg:
            try:
                import ticker_inventory
                summary = ticker_inventory.note_tickers(
                    tickers_in_msg,
                    source=source,
                    quad_regime=quad,
                    message_id=item.get("message_id"),
                )
                # Per-ticker position upsert (note_tickers takes one position;
                # tickers can have different positions in the same email, so
                # do per-ticker calls when positions diverge).
                for sym, pos in positions_by_ticker.items():
                    ticker_inventory.note_ticker(
                        sym, source=source, position=pos, quad_regime=quad,
                        message_id=item.get("message_id"),
                    )
                log.info(
                    f"  ticker_inventory: {summary['noted']}/{summary['tickers']} "
                    f"noted (source={source}, quad={quad})"
                )
            except Exception as e:
                log.warning(f"  ticker_inventory hook failed: {e}")

            # Lockstep refresh of MFR + SpotGamma + Yahoo via the unified
            # orchestrator. Per project_north_star_architecture.md, every
            # Hedgeye-originated ticker mention triggers all three tools to
            # update together. Each leg is independently wrapped — a failure
            # in one source doesn't block the others.
            try:
                import unified_refresh
                refresh_summary = unified_refresh.refresh_all_for_tickers(
                    tickers_in_msg,
                    capture_type="email_arrival",
                    spotgamma_reason=f"hedgeye_email:{source}",
                )
                log.info("  " + unified_refresh._format_summary_log(refresh_summary))
            except Exception as e:
                log.warning(f"  unified_refresh failed: {e}")

        # Product-specific typed-table parsers. Each parser reads the email
        # body itself, writes its product's structured data (ranges, walls,
        # picks, allocations) to the appropriate typed table, and stamps
        # classified_as on hedgeye_emails_raw. Runs unconditionally on the
        # subject match so emails that the classifier couldn't extract
        # tickers from still get routed.
        try:
            if re.match(r"\s*MOMO\s+Tracker\b", item.get("subject") or "", re.I):
                # MUST precede the risk_range branch: some MOMO subjects
                # contain "RR=" / "Risk Range" and were being misrouted to
                # parser_risk_range (classified_as risk_range_subject_mism).
                import parser_momo
                mo_result = parser_momo.process_one(item["id"])
                log.info(
                    f"  parser_momo: rows={mo_result.get('rows_parsed')}"
                )
            elif re.match(r"\s*CRYPTO\s+QUANT\b", item.get("subject") or "", re.I):
                import parser_crypto_quant
                cq_result = parser_crypto_quant.process_one(item["id"])
                log.info(
                    f"  parser_crypto_quant: rows={cq_result.get('rows_parsed')}"
                )
            elif source == "risk_range":
                # Inline RR dispatch. Pre-fix the bot relied on the separate
                # parser_risk_range daemon (PARSER_INTERVAL=900s default) to
                # pick this up — meaning up to 15min latency before today's
                # risk_range row appears. Now it parses the moment the email
                # lands. The daemon stays as a safety net for emails the
                # IMAP path skips (e.g. backfill).
                import parser_risk_range
                rr_result = parser_risk_range.process_one(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_risk_range: ranges={rr_result.get('rows_parsed')} "
                    f"changes={rr_result.get('signal_changes_parsed')} "
                    f"tickers={rr_result.get('ticker_count')}"
                )
            elif source == "rta":
                import parser_rta
                rta_result = parser_rta.process_one(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_rta: ticker={rta_result.get('ticker')} "
                    f"analyst={rta_result.get('analyst')} "
                    f"signal={rta_result.get('signal_type')} "
                    f"action={rta_result.get('action')}"
                )
            elif re.search(r"Actionable\s+Retail\s+Soundbites|"
                           r"KEY\s+RETAIL\s+CALLOUTS|Sunday\s+Retail\s+EDGE",
                           item.get("subject") or "", re.I):
                import parser_retail
                ret_result = parser_retail.process_one(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_retail: product={ret_result.get('product')} "
                    f"rows={ret_result.get('rows_parsed')}"
                )
            elif re.match(r"\s*Model\s+Portfolio\s+Changes\b",
                           item.get("subject") or "", re.I):
                import parser_model_portfolio
                mp_result = parser_model_portfolio.process_one(item["id"])
                log.info(
                    f"  parser_model_portfolio: "
                    f"effective={mp_result.get('effective_date')}"
                )
            elif re.search(r"Keith'?s\s+Signal\s+Longs?\s*/\s*Shorts?",
                           item.get("subject") or "", re.I):
                import parser_keiths_signals
                ks_result = parser_keiths_signals.process_one(item["id"])
                log.info(
                    f"  parser_keiths_signals: "
                    f"rows={ks_result.get('rows_parsed')}"
                )
            elif re.match(r"\s*THE\s+MACRO\s+SHOW\b",
                           item.get("subject") or "", re.I):
                import parser_macro_show
                ms_result = parser_macro_show.process_one(item["id"])
                log.info(
                    f"  parser_macro_show: rows={ms_result.get('rows_parsed')}"
                )
            elif re.match(r"\s*The\s+Call\b", item.get("subject") or "", re.I):
                import parser_the_call
                tc_result = parser_the_call.process_one(item["id"])
                log.info(
                    f"  parser_the_call: rows={tc_result.get('rows_parsed')}"
                )
            elif re.search(r"\bFinancials?\b.*\bEarnings\s+Recap\b",
                           item.get("subject") or "", re.I):
                import parser_financials
                fin_result = parser_financials.process_one(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_financials: rows={fin_result.get('rows_parsed')}"
                )
            elif re.search(r"Position\s+Monitors?\s*\||Founder'?s?\s+Choice\s*:",
                           item.get("subject") or "", re.I):
                import parser_position_monitors
                pm_result = parser_position_monitors.process_one(
                    item["id"], fan_out=False,
                )
                log.info(
                    f"  parser_position_monitors: "
                    f"product={pm_result.get('product')} "
                    f"sector={pm_result.get('sector')}"
                )
            elif re.search(r"Inflation\s+Nowcast", item.get("subject") or "", re.I):
                # Dedicated parser (extracts CPI prints / nowcast / regime).
                # MUST precede the research_notes catch-all.
                import parser_inflation_nowcast
                in_result = parser_inflation_nowcast.process_one(item["id"])
                log.info(
                    f"  parser_inflation_nowcast: "
                    f"hl={in_result.get('parsed', {}).get('headline_cpi_yoy')} "
                    f"regime={in_result.get('parsed', {}).get('regime')}"
                )
            elif re.search(
                    r"^\s*EARLY LOOK|^\s*MARKET SITUATION REPORT|"
                    r"Federal Reserve Weekly H|"
                    r"^\s*Macro Week Summary Notes|^\s*JT TAYLOR|"
                    r"^\s*Quads?/GIP Update|^\s*Hedgeye QIO|"
                    r"^\s*Monthly Commentary|Senior Loan Officer|"
                    r"^\s*Weekly Position Monitor|^\s*Updated Levels|"
                    r"^\s*Trend & Tail Levels|Top 3 Things",
                    item.get("subject") or "", re.I):
                import parser_research_notes
                rn_result = parser_research_notes.process_one(item["id"])
                log.info(
                    f"  parser_research_notes: "
                    f"product={rn_result.get('product')}"
                )
            elif source == "hedgai_signals":
                import parser_hedgai
                hg_result = parser_hedgai.process_one(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_hedgai: rows={hg_result.get('rows_parsed')}"
                )
            elif source == "etf_pro":
                import parser_etf_pro
                etf_result = parser_etf_pro.process_email(
                    item["id"], fan_out=bool(tickers_in_msg) is False,
                )
                # fan_out=True when ticker_inventory/unified_refresh did NOT run
                # above (no tickers from classifier), so the typed parser's own
                # ticker extraction triggers the MFR refresh leg.
                log.info(
                    f"  parser_etf_pro: kind={etf_result.get('kind')} "
                    f"rows={etf_result.get('rows_parsed')} "
                    f"week_of={etf_result.get('week_of')}"
                )
            elif "portfolio solutions" in subject_lower and "daily etf re-rank" in subject_lower:
                import parser_portfolio_solutions
                ps_result = parser_portfolio_solutions.process_email(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_portfolio_solutions: "
                    f"ranks={ps_result.get('ranks_parsed')} "
                    f"actions={ps_result.get('actions_parsed')}"
                )
            elif source == "signal_strength":
                import parser_signal_strength
                ss_result = parser_signal_strength.process_email(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_signal_strength: "
                    f"added={len(ss_result.get('added',[]))} "
                    f"removed={len(ss_result.get('removed',[]))}"
                )
            elif (re.match(r"\s*\d+\s+Changes?\s*:", item.get("subject") or "", re.I)
                  or re.match(r"\s*(?:Add|Remove)\b.*(?:\bto\s+(?:LONG|SHORT)"
                              r"\s+Side\b|\([A-Z][A-Z.]{0,7}\))",
                              item.get("subject") or "", re.I)
                  or re.match(r"\s*(?:Add|Remove)\s+(?:Long|Short)\b",
                              item.get("subject") or "", re.I)
                  or re.match(r"\s*Position\s+Monitor\s+Change\b",
                              item.get("subject") or "", re.I)
                  or (re.search(r"Top Stock Picks.*\b(?:REMOVE|ADD)\b",
                                item.get("subject") or "", re.I)
                      and "leaderboard" not in subject_lower)):
                # Investing Ideas roster CHANGES ("3 Changes: ...",
                # "Top Stock Picks | REMOVE: X; ADD: Y") — distinct from the
                # Leaderboard snapshot handled below.
                import parser_ii_changes
                iic_result = parser_ii_changes.process_one(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_ii_changes: rows={iic_result.get('rows_parsed')}"
                )
            elif re.match(r"\s*Investing\s+Ideas\s+Newsletter\b",
                           item.get("subject") or "", re.I):
                # Weekly full roster + price levels — distinct from the
                # Leaderboard (source==investing_ideas) handled below.
                import parser_ii_newsletter
                iin_result = parser_ii_newsletter.process_one(item["id"])
                log.info(
                    f"  parser_ii_newsletter: "
                    f"rows={iin_result.get('rows_parsed')}"
                )
            elif source == "investing_ideas":
                import parser_investing_ideas
                ii_result = parser_investing_ideas.process_email(
                    item["id"], fan_out=not bool(tickers_in_msg),
                )
                log.info(
                    f"  parser_investing_ideas: "
                    f"rows={ii_result.get('rows_parsed')}"
                )
        except Exception as e:
            log.warning(f"  product-specific parser failed: {e}")
    except Exception as e:
        log.warning(f"  ticker/refresh hook outer error: {e}")


# ───────────────── Main loop ─────────────────

def run_email_loop():
    log.info(f"Starting iCloud email parser — LOOKBACK_DAYS={LOOKBACK_DAYS}, "
             f"BACKFILL_MODE={BACKFILL_MODE}")
    conn = None

    while True:
        try:
            if conn is None:
                conn = connect_imap()
            conn.noop()
            check_email(conn)

        except (imaplib.IMAP4.abort, OSError) as e:
            log.warning(f"IMAP connection lost: {e}. Reconnecting...")
            conn = None

        except Exception as e:
            log.error(f"Email loop error: {e}")

        log.info(f"Email check done. Sleeping {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    run_email_loop()
