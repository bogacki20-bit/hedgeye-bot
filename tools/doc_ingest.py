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
    # Broker CSVs FIRST: they overwrite the real book, so they must out-rank any
    # note/report pattern. Content regexes match the Fidelity export header row.
    ("fidelity_positions", r"portfolio[_\s-]*positions.*\.csv",
     r"account\s*(number|name).*symbol"),
    # 'Accounts_History*.csv' (all accounts) AND 'History_for_Account_*.csv'
    # (one account) — the second form used to miss the filename regex and get
    # rescued only by content, which cost it its account number.
    ("fidelity_actions",
     r"accounts[_\s-]*history.*\.csv|history[_\s-]*for[_\s-]*account.*\.csv",
     r"run\s*date.*action.*symbol"),
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
    # Hedgeye's long/short-list PDF ('Updated Copy of Long Short List
    # M.D.YY.pdf'); masthead 'HEDGEYE POSITION MONITOR (MM/DD/YYYY)'.
    ("position_monitor",
     r"position_?\s*monitor|long_?[\s_]*short_?[\s_]*list|posmon",
     r"hedgeye\s+position\s+monitor"),
]

# TEXTUAL formats first (7/12 lesson, T1A doc 5: a numeric chart-axis
# string beat the 'July 10, 2026' masthead — report dates are written in
# words; bare numeric dates inside OCR'd content are usually axis noise).
# Filenames only ever carry numeric forms, so filename parsing is
# unaffected by the ordering.
_DATE_RES = [
    (re.compile(r"(january|february|march|april|may|june|july|august|"
                r"september|october|november|december)\s+(\d{1,2}),?\s+"
                r"(20\d{2})", re.I), "monname"),
    # '10 Jul 2026' — Flow Patrol's format (live miss, 7/12)
    (re.compile(r"(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|"
                r"nov|dec)[a-z]*\.?\s+(20\d{2})", re.I), "dmon"),
    (re.compile(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})"), "ymd"),
    (re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})"), "mdy"),
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
                elif order == "dmon":
                    d, y = int(m.group(1)), int(m.group(3))
                    mo = [k for k in _MONTHS
                          if k.startswith(m.group(2).lower())]
                    mo = _MONTHS[mo[0]] if mo else None
                    if mo is None:
                        continue
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


def _maybe_deep_parse(kind, row_id, note_date, text) -> str:
    """Post-store deep parses by kind (tier1alpha -> t1a_daily;
    position_monitor -> bucket sync + CHANGES block). Guarded: a parse
    failure NEVER blocks the upload reply — it appends loudly."""
    if kind == "tier1alpha":
        try:
            from tools.t1a_parse import ingest_hook
            line = ingest_hook(row_id, note_date, text)
            return "\n" + (line or "⚠ T1A deep-parse found no core fields "
                                   "(layout change? check _doc_dump)")
        except Exception as e:
            log.warning("t1a deep parse failed: %s", e)
            return f"\n⚠ T1A deep-parse error: {e}"
    if kind == "position_monitor":
        try:
            from tools.pm_parse import ingest_hook
            line = ingest_hook(row_id, note_date, text)
            return "\n" + (line or "⚠ PM deep-parse returned nothing "
                                   "(layout change? check _doc_dump)")
        except Exception as e:
            log.warning("pm deep parse failed: %s", e)
            return f"\n⚠ PM deep-parse error: {e}"
    return ""


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


# ═══════════════════ Equity Hub extract (for DAYPACK) ═══════════════════════

_TKR_CELL = re.compile(r"^[A-Z][A-Z0-9.\-/]{0,6}$")
EXTRACT_ALWAYS = ("SPX", "SPY", "QQQ", "IWM", "VIX")   # index context rows


def extract_equity_hub(text: str, tickers, max_header_rows: int = 3) -> str | None:
    """Distill a SpotGamma Equity Hub CSV (1.3M chars) to the rows for
    `tickers` (+ index rows) so the slice fits INSIDE the daypack.

    Structure-defensive (built without a full sample): the symbol column
    is found by VOTING — the column whose cells most often look like
    tickers AND hit the wanted set — never by assuming a position. Header
    block = everything above the first data row (capped). Returns the
    sliced CSV text, or None when no symbol column can be established
    (caller falls back to the omit-note, loud)."""
    if not text:
        return None
    wanted = {t.upper() for t in tickers} | set(EXTRACT_ALWAYS)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 3:
        return None

    # vote for the symbol column over the first 200 candidate rows
    votes: dict = {}
    for ln in lines[:200]:
        for i, cell in enumerate(c.strip().strip('"') for c in ln.split(",")):
            if _TKR_CELL.match(cell) and cell in wanted:
                votes[i] = votes.get(i, 0) + 1
    if not votes:
        return None
    col = max(votes, key=votes.get)

    # data starts at the first row whose symbol-cell is ticker-shaped
    data_start = None
    for idx, ln in enumerate(lines):
        cells = [c.strip().strip('"') for c in ln.split(",")]
        if len(cells) > col and _TKR_CELL.match(cells[col] or ""):
            data_start = idx
            break
    if data_start is None:
        return None
    header = lines[max(0, data_start - max_header_rows):data_start]

    hit_rows, seen = [], set()
    for ln in lines[data_start:]:
        cells = [c.strip().strip('"') for c in ln.split(",")]
        sym = cells[col] if len(cells) > col else ""
        if sym in wanted and sym not in seen:
            hit_rows.append(ln)
            seen.add(sym)
    if not hit_rows:
        return None
    missing = sorted(wanted - seen - set(EXTRACT_ALWAYS))
    out = header + hit_rows
    tail = (f"# extract: {len(hit_rows)} rows (held + index) of full file; "
            f"not in file: {' '.join(missing) if missing else 'none'}")
    return "\n".join(out + [tail])


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


def _handle_fidelity_upload(kind: str, file_name: str, data: bytes,
                            caption: str = "") -> str:
    """Broker CSV is the source of truth: write it to a temp file (keeping the
    ORIGINAL filename so the importer can read the snapshot date), overwrite the
    book (positions) or actions_log (actions), and return a diff reply. Wrapped
    so a bad file yields a clear message. NB: ingest_fidelity uses sys.exit on a
    bad header, so SystemExit is caught here too — this can never kill the
    listener."""
    import os
    import shutil
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="fid_")
    try:
        tmp = os.path.join(tmpdir, os.path.basename(file_name))
        with open(tmp, "wb") as f:
            f.write(data)
        # Operator escape hatch: caption the file FORCE to bypass the
        # stale-date / truncation refusals (the bot can't know you really did
        # liquidate). Nothing else in the caption matters here.
        force = "FORCE" in (caption or "").upper()
        if kind == "fidelity_positions":
            import ingest_fidelity
            res = ingest_fidelity.ingest(tmp, force=force)
            rec = ingest_fidelity.reconcile_book(res["snapshot_date"])

            def _fmt(xs, cap=12):
                if not xs:
                    return "none"
                return ", ".join(xs[:cap]) + (f" +{len(xs) - cap} more"
                                              if len(xs) > cap else "")
            anom = res.get("anomalies") or []
            anom_s = f" · ⚠{len(anom)} parse anomalies" if anom else ""
            # The snapshot is REPLACED, so say so out loud with the row counts
            # — 'synced' with no numbers was what hid the stale-book bug.
            repl = res.get("deleted") or 0
            accts = res.get("accounts") or []
            repl_s = (f" (replaced {repl} prior rows at this date)"
                      if repl else "")
            acct_s = (f" · accounts in file: {', '.join(accts)}"
                      if accts else "")
            return (f"📒 Book REPLACED from broker ({res['snapshot_date']})"
                    f"{repl_s}{acct_s}{' · ⚠FORCED' if force else ''} — this "
                    f"snapshot is now what every report reads. "
                    f"+{len(rec['added'])} (broker holds, bot was missing): "
                    f"{_fmt(rec['added'])} · "
                    f"−{len(rec['removed'])} (bot had, broker closed): "
                    f"{_fmt(rec['removed'])} · {rec['unchanged']} unchanged · "
                    f"{res['rows']} positions written{anom_s}")
        # fidelity_actions — three writes, each reported separately so a
        # partial failure is visible: actions_log (ML corpus + reconcile),
        # book_activity (fills history), outcomes_log (round-trip P&L).
        from tools import import_fidelity_trades
        res = import_fidelity_trades.ingest(tmp)
        errs = list(res.get("errors") or [])
        parts = [f"🧾 Actions: {res.get('rows_inserted', 0)} new, "
                 f"{res.get('rows_dup_skipped', 0)} dup, "
                 f"{res.get('rows_non_trade_skipped', 0)} non-trade, of "
                 f"{res.get('rows_seen', 0)} seen"]
        try:
            import ingest_fidelity
            act = ingest_fidelity.ingest_activity(tmp)
            parts.append(f"book_activity +{act['rows']} "
                         f"({act['dropped_spending']} spending dropped)")
        except SystemExit as e:
            errs.append(f"book_activity refused: {e}")
        except Exception as e:
            log.exception("book_activity ingest failed")
            errs.append(f"book_activity FAILED: {e}")
        try:
            # Full FIFO recompute — lot matching needs the whole history, and
            # outcomes_log is ON CONFLICT DO NOTHING, so re-running is free.
            from tools import compute_outcomes
            rc = compute_outcomes.run()
            parts.append("P&L outcomes recomputed"
                         if rc == 0 else f"⚠ outcomes recompute rc={rc}")
        except Exception as e:
            log.exception("compute_outcomes failed")
            errs.append(f"outcomes FAILED: {e}")
        err_s = f" · ⚠{'; '.join(errs)}" if errs else ""
        return " · ".join(parts) + err_s
    except SystemExit as e:
        return f"🛑 {file_name}: broker ingest refused — {e}"
    except Exception as e:
        try:
            from ingest_fidelity import StaleUploadError
        except Exception:
            StaleUploadError = ()
        if StaleUploadError and isinstance(e, StaleUploadError):
            return (f"🛑 {file_name}: NOT written — {e}\n"
                    f"(nothing changed; the book still reads the newer "
                    f"snapshot)")
        log.exception("fidelity ingest failed for %s", file_name)
        return f"🛑 {file_name}: broker ingest FAILED — {e}"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


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
    # Broker CSVs overwrite the REAL book — route to the importer. The
    # doc_uploads row above stays as the audit trail; the book write is the point.
    if kind in ("fidelity_positions", "fidelity_actions"):
        return (f"{_handle_fidelity_upload(kind, file_name, data, caption)}"
                f"\n[id {row_id}]")
    preview = re.sub(r"\s+", " ", (text or "")[:PREVIEW_CHARS]).strip()
    reply = summary_reply(kind, note_date, len(text or ""), file_name,
                          note=note, preview=preview)
    reply += f"\n[id {row_id}]"
    reply += _maybe_deep_parse(kind, row_id, note_date, text)
    return reply
