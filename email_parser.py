"""
iCloud Email Parser
Connects to iCloud IMAP, watches for Hedgeye emails,
extracts content and triggers classification.
"""

import os
import imaplib
import email
import time
import logging
import re
from email.header import decode_header
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from classifier import classify_and_extract
from notifier import send_text
from database import save_item, get_seen_email_ids

log = logging.getLogger(__name__)

ICLOUD_EMAIL    = os.environ["ICLOUD_EMAIL"]           # Bogacki20@icloud.com
ICLOUD_PASSWORD = os.environ["ICLOUD_APP_PASSWORD"]    # App-specific password from appleid.apple.com
IMAP_HOST       = "imap.mail.me.com"
IMAP_PORT       = 993
CHECK_INTERVAL  = int(os.getenv("EMAIL_CHECK_INTERVAL", "900"))  # 15 min

HEDGEYE_SENDERS = [
    "hedgeye.com",
    "tier1alpha.com",
    "email.hedgeye.com",
    "url63.hedgeye.com"
]


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
    """Decode email header value."""
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
    """Extract plain text and HTML body from email message."""
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
    """Convert HTML to readable plain text."""
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    text = extractor.get_text()
    # Clean up whitespace
    text = re.sub(r"\s{3,}", "\n\n", text)
    return text.strip()


def is_hedgeye_sender(from_addr: str) -> bool:
    """Check if email is from Hedgeye."""
    from_lower = from_addr.lower()
    return any(domain in from_lower for domain in HEDGEYE_SENDERS)


def parse_email_message(raw_bytes: bytes, uid: str) -> dict | None:
    """Parse raw email bytes into structured item dict."""
    try:
        msg = email.message_from_bytes(raw_bytes)

        from_addr = decode_mime_header(msg.get("From", ""))
        if not is_hedgeye_sender(from_addr):
            return None

        subject   = decode_mime_header(msg.get("Subject", ""))
        date_str  = msg.get("Date", "")
        try:
            timestamp = parsedate_to_datetime(date_str).isoformat()
        except Exception:
            from datetime import datetime, timezone
            timestamp = datetime.now(timezone.utc).isoformat()

        plain, html = extract_body(msg)

        # Prefer plain text; fall back to stripped HTML
        body = plain.strip() if plain.strip() else html_to_text(html)

        if len(body) < 20:
            return None

        return {
            "id":        f"email_{uid}",
            "title":     subject,
            "subject":   subject,
            "body":      body[:4000],
            "from":      from_addr,
            "timestamp": timestamp,
            "source":    "email"
        }

    except Exception as e:
        log.error(f"Error parsing email uid={uid}: {e}")
        return None


def connect_imap() -> imaplib.IMAP4_SSL:
    """Connect and authenticate to iCloud IMAP."""
    log.info("Connecting to iCloud IMAP...")
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(ICLOUD_EMAIL, ICLOUD_PASSWORD)
    conn.select("INBOX")
    log.info("IMAP connected.")
    return conn


def fetch_new_hedgeye_emails(conn: imaplib.IMAP4_SSL, seen_ids: set) -> list[dict]:
    """Search for unseen Hedgeye emails and return parsed items."""
    items = []

    try:
        # Search for unread emails from Hedgeye domains
        for domain in ["hedgeye.com", "tier1alpha.com"]:
            _, data = conn.search(None, f'(UNSEEN FROM "{domain}")')
            uids = data[0].split() if data[0] else []

            for uid in uids:
                uid_str = uid.decode()
                email_id = f"email_{uid_str}"

                if email_id in seen_ids:
                    continue

                _, raw = conn.fetch(uid, "(RFC822)")
                if not raw or not raw[0]:
                    continue

                raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else None
                if not raw_bytes:
                    continue

                item = parse_email_message(raw_bytes, uid_str)
                if item:
                    items.append(item)
                    seen_ids.add(email_id)

    except imaplib.IMAP4.error as e:
        log.error(f"IMAP error: {e}")

    return items


def run_email_loop():
    """Main email polling loop."""
    log.info("Starting iCloud email parser...")
    seen_ids = get_seen_email_ids()
    conn = None

    while True:
        try:
            if conn is None:
                conn = connect_imap()

            # Keep connection alive
            conn.noop()

            new_emails = fetch_new_hedgeye_emails(conn, seen_ids)

            if new_emails:
                log.info(f"Found {len(new_emails)} new Hedgeye emails")
                for item in new_emails:
                    log.info(f"  Processing: {item['title'][:70]}")
                    item = classify_and_extract(item)
                    save_item(item)

                    # Immediate alert for trade signals
                    if item.get("classified_type") == "trade_signal" and item.get("action_required"):
                        tickers = item.get("tickers", [])
                        if tickers:
                            ticker_str = ", ".join(
                                f"{t.get('direction','Long')} {t.get('ticker','?')} ({t.get('conviction','')})"
                                for t in tickers[:3]
                            )
                            send_text(f"🚨 Hedgeye signal via email:\n{ticker_str}\n\n{item.get('summary','')[:120]}")
            else:
                log.info("No new Hedgeye emails.")

        except (imaplib.IMAP4.abort, OSError) as e:
            log.warning(f"IMAP connection lost: {e}. Reconnecting...")
            conn = None

        except Exception as e:
            log.error(f"Email loop error: {e}")

        log.info(f"Email check done. Sleeping {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_email_loop()
