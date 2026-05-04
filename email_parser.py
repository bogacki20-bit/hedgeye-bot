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
import threading
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


# ─── IMAP fetch deadline helper ─────────────────────────────────────────────
# 2026-05-03 incidents: iCloud IMAP can silently hang individual fetches with
# no error and no log output. Two prior attempts to fix via socket-level
# timeouts failed:
#   - IMAP4_SSL(timeout=60) constructor kwarg alone — bot still hung silently.
#   - socket.setdefaulttimeout(60) globally — broke SSL with BAD_LENGTH errors.
# This third approach uses an application-level deadline that doesn't touch
# socket flags: a threading.Timer that calls conn.shutdown() if a fetch runs
# longer than the deadline. The blocked fetch then sees the socket close and
# raises an OSError, which the existing try/except catches and reconnects.
class IMAPFetchDeadline:
    """Context manager that closes the IMAP connection if it doesn't return in time."""
    def __init__(self, conn, seconds: int = 30):
        self.conn = conn
        self.seconds = seconds
        self.timer: threading.Timer | None = None
        self.fired = False

    def _fire(self):
        self.fired = True
        try:
            # Calling shutdown() on imaplib unblocks the fetch by closing the socket.
            self.conn.shutdown()
        except Exception:
            pass  # best effort — socket may already be torn

    def __enter__(self):
        self.timer = threading.Timer(self.seconds, self._fire)
        self.timer.daemon = True
        self.timer.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.timer is not None:
            self.timer.cancel()
        # If we fired the deadline, raise so the outer try/except handles it.
        # Don't suppress an existing exception.
        if self.fired and exc_type is None:
            raise TimeoutError(f"IMAP fetch exceeded {self.seconds}s deadline")
        return False

log = logging.getLogger(__name__)

ICLOUD_EMAIL    = os.environ["ICLOUD_EMAIL"]
ICLOUD_PASSWORD = os.environ["ICLOUD_APP_PASSWORD"]
IMAP_HOST       = "imap.mail.me.com"
IMAP_PORT       = 993
CHECK_INTERVAL  = int(os.getenv("EMAIL_CHECK_INTERVAL", "900"))

LOOKBACK_DAYS   = int(os.getenv("EMAIL_LOOKBACK_DAYS", "90"))
BACKFILL_MODE   = os.environ.get("BACKFILL_MODE", "off").lower()

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
    """Connect to iCloud IMAP with a hard socket timeout.

    The timeout applies to the underlying socket, so subsequent search/fetch
    calls inherit it. Without this, a stalled iCloud server causes Python's
    imaplib to block forever with no error and no log output (root cause of
    the 2026-05-03 silent-hang incident — bot looked alive but processed
    zero emails after the "Email check: N candidates" line).
    """
    log.info("Connecting to iCloud IMAP...")
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=60)
    conn.login(ICLOUD_EMAIL, ICLOUD_PASSWORD)
    conn.select("INBOX")
    log.info("IMAP connected.")
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

    for uid in sorted(candidate_uids):
        uid_str = uid.decode()

        # Cheap header peek including Message-ID, so we can dedup BEFORE the
        # expensive full-body fetch. This is the structural fix that prevents
        # blasting iCloud with 1500+ full RFC822 fetches on a fresh lake.
        from_addr, subject, msg_id = "", "", ""
        try:
            log.info(f"  [{uid_str}] header peek...")  # heartbeat
            with IMAPFetchDeadline(conn, seconds=30):
                _, hdr_data = conn.fetch(uid, "(BODY[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT)])")
            hdr_bytes = hdr_data[0][1] if hdr_data and isinstance(hdr_data[0], tuple) else b""
            hdr_msg   = email.message_from_bytes(hdr_bytes)
            from_addr = decode_mime_header(hdr_msg.get("From", ""))
            subject   = decode_mime_header(hdr_msg.get("Subject", "(no subject)"))
            msg_id    = decode_mime_header(hdr_msg.get("Message-ID", "")).strip().strip("<>")
        except Exception as e:
            log.warning(f"  [{uid_str}] header peek failed ({e}) — skipping")
            # If header peek fails, mostly likely the connection is dead.
            # Bubble it up so the outer loop reconnects.
            raise

        # Sender filter (cheap, before any further work)
        if not is_hedgeye_sender(from_addr):
            log.info(f"  [{uid_str}] skip — non-Hedgeye sender {from_addr!r}")
            continue

        # Lake dedup BEFORE the expensive full fetch
        if msg_id and db_pg.is_email_seen(msg_id):
            log.info(f"  [{uid_str}] skip (already in lake) | subject={subject[:60]!r}")
            continue

        log.info(f"  [{uid_str}] NEW | from={from_addr!r} | subject={subject[:60]!r}")

        # Fetch the full message and insert into the lake.
        try:
            log.info(f"  [{uid_str}] full body fetch...")  # heartbeat
            with IMAPFetchDeadline(conn, seconds=60):
                _, raw = conn.fetch(uid, "(RFC822)")
            if not raw or not raw[0]:
                log.warning(f"  [{uid_str}] empty fetch result")
                continue
            raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else None
            if not raw_bytes:
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
                log.info(f"  [{uid_str}] skip (already in lake) | subject={subject[:70]!r}")
                continue

            log.info(f"  [{uid_str}] NEW → lake | from={from_addr!r} | "
                     f"subject={subject[:70]!r} | status={parsed['content_status']}")
            new_count += 1
            _process_new_email(parsed)

        except Exception as e:
            log.error(f"  [{uid_str}] fetch/process error: {e}")

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
        # Recommender migration deferred to a separate task — both
        # `portfolio.py` and `recommender.py` still talk to the (now
        # abandoned) SQLite DB and need their own focused port to db_pg.
        # Until then, the raw trade-signal email body is still pushed to
        # Telegram by the send_pushover call above; we just don't generate
        # the sized "BUY $X SPY in IRA" recommendation message yet.
        log.info(
            f"  trade_signal detected: {item.get('direction', '?')} "
            f"{item.get('ticker')} (conviction={item.get('conviction', '?')!r}) "
            f"— sized recommendation deferred (recommender migration pending)"
        )


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
