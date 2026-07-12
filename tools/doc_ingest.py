"""
doc_ingest.py — Telegram document-upload ingest (sprint P2, 2026-07-11).

Flow: operator downloads a report on the phone -> sends the FILE to the bot
in Telegram -> this module downloads it (getFile), extracts text (pypdf for
PDFs; text/markdown/html/csv decoded directly), CLASSIFIES it, stores a
doc_uploads row, and replies a loud summary. The send IS the operator
action (same doctrine as trade decisions) — no extra CONFIRM.

Kinds (filename + content keywords, pure + fixture-tested):
  founders_note_am / founders_note_pm / founders_note  (SpotGamma)
  flow_patrol                                          (SpotGamma)
  equity_hub                                           (SpotGamma — replaces
                                                        the DEAD 5K scrape)
  tier1alpha                                           (Tier One Alpha)
  other                                                (stored, flagged)

note_date is parsed best-effort from the filename/first lines; when it
can't be found the reply says 'undated' out loud (a fact without a date
isn't a fact) — the row still stores with uploaded_at.

This table is the staging corpus for the future RAG layer. Deeper
tier1alpha parsing (1M/3M vol, CTA levels -> regime flags) is a later
build on top of these rows.
"""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime

log = logging.getLogger("doc_ingest")

MAX_FILE_MB = 20            # Telegram bot-API getFile ceiling
PREVIEW_CHARS = 220

# AM/PM matching: (?<![a-z])am(?![a-z]) — plain \b fails on snake_case
# (underscore is a word char) and a bare 'am' would hit 'spotgAMma'.
_AM = r"(?<![a-z])(?:am|morning)(?![a-z])"
_PM = r"(?<![a-z])(?:pm|evening)(?![a-z])"
_KIND_PATTERNS = [
    # (kind, filename regex, content regex) — first hit wins, top-down
    ("founders_note_am",
     rf"founders?_?\s*note.*{_AM}|{_AM}.*founders?",
     rf"founder'?s\s+note.*{_AM}|{_AM}[\s\w]*founder'?s\s+note"),
    ("founders_note_pm",
     rf"founders?_?\s*note.*{_PM}|{_PM}.*founders?",
     rf"founder'?s\s+note.*{_PM}|{_PM}[\s\w]*founder'?s\s+note"),
    ("founders_note", r"founders?_?\s*note", r"founder'?s\s+note"),
    ("flow_patrol", r"flow_?\s*patrol", r"flow\s+patrol"),
    ("equity_hub", r"equity_?\s*hub", r"equity\s+hub"),
    ("tier1alpha",
     r"tier_?\s*(one|1)_?\s*alpha|(?<![a-z0-9])t1a(?![a-z0-9])",
     r"tier\s+(one|1)\s+alpha"),
]

_DATE_RES = [
    (re.compile(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})"), "mdy"),
    (re.compile(r"(january|february|march|april|may|june|july|august|"
                r"september|october|november|december)\s+(\d{1,2}),?\s+"
                r"(20\d{2})", re.I), "monname"),
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


# ═══════════════════════ pure logic (fixture-tested) ════════════════════════

def classify_upload(file_name: str | None, text: str | None) -> str:
    """Kind from filename first (operator named it), then content head.
    'other' is a stored, flagged result — never a silent drop."""
    fn = (file_name or "").lower()
    head = (text or "")[:4000]
    for kind, fn_re, ct_re in _KIND_PATTERNS:
        if re.search(fn_re, fn, re.I):
            return kind
    for kind, fn_re, ct_re in _KIND_PATTERNS:
        if re.search(ct_re, head, re.I):
            return kind
    return "other"


def parse_note_date(file_name: str | None, text: str | None):
    """Best-effort date from filename, then the content head. None is a
    LOUD result (reply says 'undated'), never a guess."""
    for hay in ((file_name or ""), (text or "")[:2000]):
        for rx, order in _DATE_RES:
            m = rx.search(hay)
            if not m:
                continue
            try:
                if order == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                elif order == "mdy":
                    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    mo = _MONTHS[m.group(1).lower()]
                    d, y = int(m.group(2)), int(m.group(3))
                return date(y, mo, d)
            except (ValueError, KeyError):
                continue
    return None


def extract_text(file_name: str | None, data: bytes) -> tuple:
    """(text, note) — PDFs via pypdf; text-ish formats decoded. Unknown
    binary returns ('', 'unextractable') so the row still lands, loud."""
    fn = (file_name or "").lower()
    if fn.endswith(".pdf") or data[:5] == b"%PDF-":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
            return text.strip(), None
        except Exception as e:
            log.warning("pdf extraction failed for %s: %s", file_name, e)
            return "", f"pdf extraction failed: {e}"
    if any(fn.endswith(x) for x in (".txt", ".md", ".csv", ".html", ".htm",
                                    ".json")):
        try:
            return data.decode("utf-8", errors="replace").strip(), None
        except Exception as e:
            return "", f"decode failed: {e}"
    # last resort: try utf-8; binary junk gets flagged
    try:
        text = data.decode("utf-8")
        return text.strip(), None
    except UnicodeDecodeError:
        return "", "unextractable (unknown binary format)"


def summary_reply(kind, note_date, chars, file_name, note=None,
                  preview="") -> str:
    d = str(note_date) if note_date else "UNDATED ⚠ (a fact without a date)"
    lines = [f"📥 stored: {file_name or '?'}",
             f"kind={kind}{' ⚠unclassified' if kind == 'other' else ''} · "
             f"date={d} · {chars:,} chars"
             + (f" · ⚠ {note}" if note else "")]
    if preview:
        lines.append(f"head: {preview}")
    lines.append("(staged for RAG corpus — doc_uploads)")
    return "\n".join(lines)


# ═══════════════════════ Telegram download + store ══════════════════════════

def _download(token: str, file_id: str) -> bytes | None:
    import requests
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getFile",
                         params={"file_id": file_id}, timeout=30)
        r.raise_for_status()
        path = r.json()["result"]["file_path"]
        f = requests.get(f"https://api.telegram.org/file/bot{token}/{path}",
                         timeout=60)
        f.raise_for_status()
        return f.content
    except Exception as e:
        log.error("telegram file download failed: %s", e)
        return None


def store_upload(file_name, kind, note_date, text, meta=None) -> int | None:
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(
            """INSERT INTO doc_uploads
                 (file_name, kind, note_date, char_count, content_text, meta)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (file_name, kind, note_date, len(text or ""), text,
             json.dumps(meta or {})))
        row_id = cur.fetchone()[0]
        c.commit()
    return row_id


def handle_telegram_document(token: str, document: dict,
                             caption: str = "") -> str:
    """Full ingest for one Telegram document message. Returns the reply
    text. Never raises — every failure path returns a loud message."""
    file_name = document.get("file_name") or "unnamed"
    size = document.get("file_size") or 0
    if size > MAX_FILE_MB * 1024 * 1024:
        return (f"🛑 {file_name}: {size / 1048576:.0f}MB exceeds Telegram's "
                f"{MAX_FILE_MB}MB bot download cap — split it or email it.")
    data = _download(token, document.get("file_id", ""))
    if data is None:
        return f"🛑 {file_name}: download from Telegram failed — resend."
    text, note = extract_text(file_name, data)
    hint = f"{file_name} {caption or ''}"
    kind = classify_upload(hint, text)
    note_date = parse_note_date(hint, text)
    try:
        row_id = store_upload(file_name, kind, note_date, text,
                              meta={"caption": caption or None,
                                    "size_bytes": size,
                                    "extract_note": note,
                                    "received": datetime.utcnow().isoformat()})
    except Exception as e:
        log.exception("doc_uploads store failed")
        return f"🛑 {file_name}: parsed OK but store FAILED: {e}"
    preview = re.sub(r"\s+", " ", (text or "")[:PREVIEW_CHARS]).strip()
    reply = summary_reply(kind, note_date, len(text or ""), file_name,
                          note=note, preview=preview)
    return reply + f"\n[id {row_id}]"
