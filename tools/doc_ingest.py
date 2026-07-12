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
    # SpotGamma names Equity Hub exports '<idx>_data-table_<date>.csv'
    # (operator-confirmed on the live 7/11 upload) — no 'equity hub' text
    # anywhere in the file.
    ("equity_hub", r"equity_?\s*hub|data[-_]?table", r"equity\s+hub"),
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


# ═══════════════════════ photo / Vision-OCR ingest ══════════════════════════
# Operator reality (7/11 night): Tier One Alpha arrives as SCREENSHOTS.
# Photo -> getFile download -> Claude vision TRANSCRIBES (extraction, not
# judgment — the LLM writes nothing but the transcribed text into the
# staging table) -> same classify/store path as any document.
# Composes with the paste buffer: while DOC START is active, each OCR'd
# screenshot appends as a chunk; DOC END stitches them into ONE row.
# A lone photo (no buffer) stores immediately as its own row.

# claude-sonnet-4-20250514 404'd live on 7/11 (model retired) — pin a
# current vision-capable model, env-overridable without a deploy.
import os as _os_mod
OCR_MODEL = _os_mod.getenv("OCR_MODEL", "claude-haiku-4-5-20251001")
OCR_MAX_TOKENS = 4000
OCR_PROMPT = ("Transcribe ALL text visible in this image, exactly and "
              "completely, preserving line structure and numbers. Output "
              "ONLY the transcribed text — no commentary, no summary.")


def ocr_image(data: bytes, mime: str = "image/jpeg") -> tuple:
    """(text, note) — Claude vision transcription. Loud note on failure."""
    import base64
    try:
        import anthropic
        import os as _os
        client = anthropic.Anthropic(api_key=_os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(
            model=OCR_MODEL, max_tokens=OCR_MAX_TOKENS,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime,
                    "data": base64.b64encode(data).decode()}},
                {"type": "text", "text": OCR_PROMPT}]}])
        text = "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()
        if not text:
            return "", "vision returned no text"
        return text, None
    except Exception as e:
        log.warning("vision OCR failed: %s", e)
        return "", f"vision OCR failed: {e}"


def handle_telegram_photo(token: str, photos: list, caption: str = "") -> str:
    """One Telegram photo message -> OCR -> buffer-append (if DOC START
    active) or immediate classified doc_uploads row. Never raises."""
    if not photos:
        return "🛑 photo message with no sizes — resend."
    largest = max(photos, key=lambda p: p.get("file_size") or 0)
    data = _download(token, largest.get("file_id", ""))
    if data is None:
        return "🛑 photo download from Telegram failed — resend."
    text, note = ocr_image(data)
    if not text:
        return f"🛑 screenshot unreadable: {note}"

    raw = _bs_get(BUFFER_KEY)
    buf = json.loads(raw) if raw else None
    if buf:                                 # active DOC buffer: append
        buf["parts"].append(text)
        _bs_set(BUFFER_KEY, json.dumps(buf))
        return (f"📷→📋 OCR'd into buffer ({len(buf['parts'])} chunks, "
                f"{len(text):,} chars this shot) — DOC END when done.")

    hint = caption or "screenshot"
    kind = classify_upload(hint, text)
    note_date = parse_note_date(hint, text)
    try:
        row_id = store_upload(hint, kind, note_date, text,
                              meta={"source_detail": "photo_ocr",
                                    "ocr_note": note})
    except Exception as e:
        log.exception("photo store failed")
        return f"🛑 OCR'd but store FAILED: {e}"
    preview = re.sub(r"\s+", " ", text[:PREVIEW_CHARS]).strip()
    return (summary_reply(kind, note_date, len(text), hint, note=note,
                          preview=preview) + f"\n[id {row_id} · vision-OCR]")


# ═══════════════════════ paste-capture (DOC START/END) ══════════════════════
# Phone reality: copy-paste of a long report arrives as ~25 separate text
# messages (Telegram splits at 4096). Buffer mode stitches them into ONE
# doc_uploads row: DOC START [hint] -> paste everything -> DOC END. While
# active this handler runs FIRST in the dispatch chain and claims every
# message silently (a pasted line that looks like 'TARGET ...' must never
# trigger a real handler). TTL guards an abandoned buffer.

BUFFER_KEY = "doc_paste_buffer"
BUFFER_TTL_MIN = 30
_ACK_EVERY = 10          # quiet ack every N chunks so the operator sees life


def _bs_get(key):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
        r = cur.fetchone()
        return r[0] if r and r[0] else None


def _bs_set(key, val):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO bot_state (key,value,updated_at) "
                    "VALUES (%s,%s,NOW()) ON CONFLICT (key) DO UPDATE "
                    "SET value=EXCLUDED.value, updated_at=NOW()", (key, val))
        c.commit()


def handle_doc_buffer(text):
    """Dispatch-chain handler (registered FIRST). Returns a reply when it
    owns the message, None to fall through. Owns: DOC START/END/CANCEL
    always; EVERY message while a buffer is active."""
    if text is None:
        return None
    up = text.strip().upper()
    raw = _bs_get(BUFFER_KEY)
    buf = json.loads(raw) if raw else None

    if buf:
        try:
            started = datetime.fromisoformat(buf["started_at"])
            if (datetime.utcnow() - started).total_seconds() > BUFFER_TTL_MIN * 60:
                _bs_set(BUFFER_KEY, "")
                buf = None
        except Exception:
            _bs_set(BUFFER_KEY, "")
            buf = None
        if buf is None and up not in ("DOC END", "DOC CANCEL"):
            # expired mid-paste — loud, don't swallow the message silently
            if up.startswith("DOC START"):
                pass                       # falls through to fresh START below
            else:
                return ("⏱️ DOC buffer expired (>30 min) — paste lost. "
                        "DOC START again.")

    if up.startswith("DOC START"):
        hint = text.strip()[len("DOC START"):].strip()
        _bs_set(BUFFER_KEY, json.dumps(
            {"started_at": datetime.utcnow().isoformat(),
             "hint": hint, "parts": []}))
        return (f"📋 buffering{' (' + hint + ')' if hint else ''} — paste "
                f"everything, then DOC END. (DOC CANCEL to abort; "
                f"{BUFFER_TTL_MIN} min TTL)")

    if buf is None:
        return None                        # no buffer, not a DOC command

    if up == "DOC CANCEL":
        _bs_set(BUFFER_KEY, "")
        return f"📋 buffer discarded ({len(buf['parts'])} chunks)."

    if up == "DOC END":
        _bs_set(BUFFER_KEY, "")
        body = "\n".join(buf["parts"])
        if not body.strip():
            return "📋 buffer was empty — nothing stored."
        hint = buf.get("hint") or ""
        kind = classify_upload(hint or None, body)
        note_date = parse_note_date(hint or None, body)
        try:
            row_id = store_upload(hint or f"paste_{len(buf['parts'])}chunks",
                                  kind, note_date, body,
                                  meta={"source_detail": "paste",
                                        "chunks": len(buf["parts"])})
        except Exception as e:
            log.exception("paste store failed")
            return f"🛑 paste parsed but store FAILED: {e}"
        preview = re.sub(r"\s+", " ", body[:PREVIEW_CHARS]).strip()
        return (summary_reply(kind, note_date, len(body),
                              hint or "pasted document", preview=preview)
                + f"\n[id {row_id} · {len(buf['parts'])} chunks stitched]")

    # active buffer: append silently (quiet ack every N chunks)
    buf["parts"].append(text)
    _bs_set(BUFFER_KEY, json.dumps(buf))
    n = len(buf["parts"])
    return f"📋 {n} chunks…" if n % _ACK_EVERY == 0 else ""


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
