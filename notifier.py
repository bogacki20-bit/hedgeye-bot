import logging
import os
import re
import requests

log = logging.getLogger("notifier")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Any Telegram bot token, ours or not, in any string we are about to log.
# 2026-08-25 audit: requests' HTTPError message embeds the full request URL,
# so every failure line wrote the LIVE TOKEN to logs\scanner_*.log (198
# occurrences in the 8/20-8/24 files alone). Redact at the point of logging.
_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_\-]{10,}")


def _redact(s) -> str:
    s = _TOKEN_RE.sub("bot<REDACTED>", str(s))
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if tok:
        s = s.replace(tok, "<REDACTED>")
    return s


def _chunks(text):
    """telegram_handler._chunk_message — the ONE chunker (splits on line
    boundaries at Telegram's 4096 limit). Imported lazily so notifier keeps
    working even if telegram_handler cannot load; the fallback sends the text
    unchunked, which is exactly the pre-2026-08-25 behaviour."""
    try:
        from telegram_handler import _chunk_message
        return _chunk_message(text)
    except Exception as e:
        log.warning("chunker unavailable (%s) — sending unchunked", e)
        return [text] if text else []


def send_telegram(title, message, priority=1):
    """
    Send a Telegram notification. Returns True only if EVERY chunk landed.

    2026-08-25 (the ~37%%-silent-sends fix):
      * over-4096 messages are CHUNKED on line boundaries instead of being
        rejected whole with an HTTP 400 and lost;
      * every non-2xx logs the API's OWN `description` plus the first 200
        chars of the failing payload — the HTTP status alone kept the real
        cause uncapturable for a month;
      * the bot token is redacted from every logged string.
    parse_mode stays Markdown deliberately — if the captured descriptions
    name entity-parse failures, changing it is a separate, evidenced fix.

    `priority` is accepted for backwards compatibility with the old Pushover
    signature but is ignored — Telegram has no priority levels.
    """
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set; cannot send.")
        return False

    text = f"*{title}*\n\n{message}" if title else message
    parts = _chunks(text)
    if not parts:
        log.warning("Telegram send skipped: empty message (title=%r)", title)
        return False

    ok = True
    for idx, chunk in enumerate(parts, 1):
        tag = f" [{idx}/{len(parts)}]" if len(parts) > 1 else ""
        try:
            response = requests.post(
                TELEGRAM_API.format(token=token),
                data={
                    "chat_id":    chat_id,
                    "text":       chunk,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
        except Exception as e:
            log.error("Telegram send failed%s (transport): %s",
                      tag, _redact(e))
            ok = False
            continue
        if response.ok:
            log.info(f"Telegram sent{tag}: {title}")
            continue
        # The API's own reason lives in `description` in the JSON body.
        try:
            desc = (response.json() or {}).get("description") or ""
        except Exception:
            desc = (response.text or "")[:200]
        log.error("Telegram send failed%s: http %s — %s — payload[:200]=%r",
                  tag, response.status_code, _redact(desc),
                  _redact(chunk[:200]))
        # EVIDENCE-GATED fallback (2026-08-25): the E1 logging captured the
        # 400s' own words — "can't parse entities" — i.e. a stray _ * [ in a
        # ticker or company name kills the whole message under Markdown.
        # Per the E3 rule that fix is made only on that evidence: when the
        # API NAMES an entity-parse failure, resend the same chunk as plain
        # text. An unformatted alert beats a vanished one. Markdown remains
        # the default for everything else.
        if response.status_code == 400 and "can't parse entities" in desc:
            try:
                retry = requests.post(
                    TELEGRAM_API.format(token=token),
                    data={"chat_id": chat_id, "text": chunk},
                    timeout=10,
                )
                if retry.ok:
                    log.info(f"Telegram sent{tag} PLAIN (markdown entity "
                             f"failure, delivered unformatted): {title}")
                    continue
                try:
                    rdesc = (retry.json() or {}).get("description") or ""
                except Exception:
                    rdesc = (retry.text or "")[:200]
                log.error("Telegram plain-text retry failed%s: http %s — %s",
                          tag, retry.status_code, _redact(rdesc))
            except Exception as e:
                log.error("Telegram plain-text retry failed%s (transport): %s",
                          tag, _redact(e))
        ok = False
    return ok


send_pushover = send_telegram
