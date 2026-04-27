"""
iCloud Email Parser
Connects to iCloud IMAP, watches for Hedgeye emails, classifies them,
and pushes notifications. Dedupes via a dedicated processed_emails
SQLite table keyed by IMAP UID.

Environment:
  BACKFILL_MODE = off | silent | notify
    off    (default) — normal operation, Pushover for every new email
    silent           — process and store everything, NO Pushover
    notify           — process AND Pushover (use sparingly; can flood)

  EMAIL_CHECK_INTERVAL — poll interval in seconds (default 900 / 15 min)
"""

import os
import imaplib
import email
import time
import logging
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from classifier import classify_and_extract
from notifier import send_pushover
from database import save_item, get_conn

log = logging.getLogger(__name__)

ICLOUD_EMAIL    = os.environ["ICLOUD_EMAIL"]
ICLOUD_PASSWORD = os.environ["ICLOUD_APP_PASSWORD"]
IMAP_HOST       = "imap.mail.me.com"
IMAP_PORT       = 993
CHECK_INTERVAL  = int(os.getenv("EMAIL_CHECK_INTERVAL", "900"))

LOOKBACK_DAYS   = 90
BACKFILL_MODE   = os.environ.get("BACKFILL_MODE", "off").lower()

# Substring keywords for the IMAP FROM search and the post-fetch sender check.
# IMAP FROM is substring-match, so "hedgeye.com" catches every user@hedgeye.com.
HEDGEYE_KEYWORDS = ["hedgeye.com", "tier1alpha"]


# ───────────────── SQLite dedup helpers ─────────────────

def init_processed_table():
    """Create processed_emails (idempotent). Keyed by IMAP UID."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_emails (
                uid           TEXT PRIMARY KEY,
                subject       TEXT,
                sender        TEXT,
                processed_at  TEXT DEFAULT (datetime('now'))
            )
        """)


def is_processed(uid: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_emails WHERE uid = ?", (uid,)
        ).fetchone()
        return row is not None


def mark_processed(uid: str, subject: str = "", sender: str = ""):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO processed_emails (uid, subject, sender)
            VALUES (?, ?, ?)
        """, (uid, subject, sender))


# ───────────────── Email parsing ─────────────────

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
    """Match any sender containing one of the HEDGEYE_KEYWORDS."""
    from_lower = (from_addr or "").lower()
    return any(kw in from_lower for kw in HEDGEYE_KEYWORDS)


def parse_email_message(raw_bytes: bytes, uid: str) -> dict | None:
    """Parse raw email bytes into structured item dict. Logs reason on drop."""
    try:
        msg = email.message_from_bytes(raw_bytes)

        from_addr = decode_mime_header(msg.get("From", ""))
        if not is_hedgeye_sender(from_addr):
            log.warning(f"  [{uid}] dropped — sender {from_addr!r} not a Hedgeye keyword match")
            return None

        subject  = decode_mime_header(msg.get("Subject", ""))
        date_str = msg.get("Date", "")
        try:
            timestamp = parsedate_to_datetime(date_str).isoformat()
        except Exception:
            from datetime import timezone
            timestamp = datetime.now(timezone.utc).isoformat()

        plain, html = extract_body(msg)
        body = plain.strip() if plain.strip() else html_to_text(html)

        if len(body) < 20:
            log.warning(f"  [{uid}] dropped — body too short ({len(body)} chars), subject={subject!r}")
            return None

        return {
            "id":        f"email_{uid}",
            "title":     subject,
            "subject":   subject,
            "body":      body[:4000],
            "from":      from_addr,
            "timestamp": timestamp,
            "source":    "email",
        }

    except Exception as e:
        log.error(f"  [{uid}] parse error: {e}")
        return None


# ───────────────── IMAP fetch ─────────────────

def connect_imap() -> imaplib.IMAP4_SSL:
    log.info("Connecting to iCloud IMAP...")
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(ICLOUD_EMAIL, ICLOUD_PASSWORD)
    conn.select("INBOX")
    log.info("IMAP connected.")
    return conn


def check_email(conn: imaplib.IMAP4_SSL) -> int:
    """
    One polling cycle. Searches by SINCE date (LOOKBACK_DAYS), uses the
    processed_emails SQLite table for dedup, and respects BACKFILL_MODE
    when deciding whether to send Pushover.
    Returns the number of newly processed emails.
    """
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

    processed_count = 0

    for uid in sorted(candidate_uids):
        uid_str = uid.decode()

        # Cheap header fetch so we log every email we see, even skipped ones
        from_addr, subject = "", ""
        try:
            _, hdr_data = conn.fetch(uid, "(BODY[HEADER.FIELDS (FROM SUBJECT)])")
            hdr_bytes   = hdr_data[0][1] if hdr_data and isinstance(hdr_data[0], tuple) else b""
            hdr_msg     = email.message_from_bytes(hdr_bytes)
            from_addr   = decode_mime_header(hdr_msg.get("From", ""))
            subject     = decode_mime_header(hdr_msg.get("Subject", "(no subject)"))
        except Exception as e:
            log.warning(f"  [{uid_str}] could not fetch headers: {e}")

        if is_processed(uid_str):
            log.info(f"  [{uid_str}] skip (already processed) | from={from_addr!r} | subject={subject[:70]!r}")
            continue

        log.info(f"  [{uid_str}] NEW | from={from_addr!r} | subject={subject[:70]!r}")

        # Fetch full message
        try:
            _, raw = conn.fetch(uid, "(RFC822)")
            if not raw or not raw[0]:
                log.warning(f"  [{uid_str}] empty fetch result")
                continue
            raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else None
            if not raw_bytes:
                continue

            item = parse_email_message(raw_bytes, uid_str)
            if not item:
                # parse_email_message already logged the reason; mark processed
                # so we don't keep reprocessing the same drop every 15 min.
                mark_processed(uid_str, subject=subject, sender=from_addr)
                continue

            _process_item(item)
            mark_processed(uid_str, subject=item["subject"], sender=item["from"])
            processed_count += 1

        except Exception as e:
            log.error(f"  [{uid_str}] fetch/process error: {e}")

    if BACKFILL_MODE in ("silent", "notify") and processed_count > 0:
        log.info(f"BACKFILL COMPLETE — processed {processed_count} email(s) this cycle "
                 f"(mode={BACKFILL_MODE})")

    return processed_count


def _process_item(item: dict):
    """Classify, save, and (mode-permitting) push notifications."""
    notify = BACKFILL_MODE != "silent"

    if notify:
        send_pushover(item["subject"] or "Hedgeye email", item["body"])

    item = classify_and_extract(item)
    save_item(item)

    if item.get("classified_type") == "trade_signal" and item.get("ticker"):
        from recommender import recommend_from_signal, format_for_pushover
        rec = recommend_from_signal(item)
        if rec and notify:
            title, msg = format_for_pushover(rec)
            send_pushover(title, msg)
            log.info(f"  Recommendation #{rec['id']}: {rec['action']} {rec['ticker']}")
        elif rec:
            log.info(f"  Recommendation #{rec['id']} (silent): {rec['action']} {rec['ticker']}")


# ───────────────── Main loop ─────────────────

def run_email_loop():
    log.info(f"Starting iCloud email parser — LOOKBACK_DAYS={LOOKBACK_DAYS}, BACKFILL_MODE={BACKFILL_MODE}")
    init_processed_table()
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
