"""
daypack.py — DAYPACK: the whole day in ONE file for LLM upload
(operator spec, Sat 7/11 late: "pull those reports in one function so I
can send to an LLM chat for the day").

One Telegram command -> one .txt attachment:
  1. REPORT v4 upload mode (full tgt/src, full DIV, position table)
  2. a FRESH REPORT NOW (live prices — ~20-30s of the build time)
  3. every doc_uploads row from the last 24h (founders notes, flow patrol,
     tier1alpha, …), each under a labeled header with kind/date/chars.
     Per-doc cap DOC_CAP chars, truncated LOUD; monsters over OMIT_OVER
     (the 1.3M-char Equity Hub CSV) are omitted with a loud note — upload
     that file to the chat separately, it would drown everything else.

Sections are individually guarded: a failing section prints its error in
place, the pack still assembles (no-silent-failures). Stored to
report_rows kind='daypack'. Read-only otherwise.

Telegram: DAYPACK
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("daypack")

SENTINEL = "DAYPACK"
DOC_CAP = 40_000          # per-doc chars included in the pack
OMIT_OVER = 150_000       # bigger than this -> omit with a loud note
LOOKBACK_HOURS = 24


def _hdr(title: str) -> str:
    return f"\n{'=' * 8} {title} {'=' * 8}\n"


def build_daypack() -> str:
    parts = [f"DAYPACK {date.today()} — full-day fact bundle for LLM "
             f"upload (REPORT + REPORT NOW + last-{LOOKBACK_HOURS}h uploads)"]

    try:
        from tools.report import build_report_v4
        parts.append(_hdr("REPORT v4 (upload mode)"))
        parts.append(build_report_v4(kind="daypack", verbose=True,
                                     persist_snapshot=False))
    except Exception as e:
        log.exception("daypack: REPORT section failed")
        parts.append(_hdr("REPORT v4") + f"unavailable ({e})")

    try:
        from tools.report_now import build_report_now
        parts.append(_hdr("REPORT NOW (live)"))
        parts.append(build_report_now())
    except Exception as e:
        log.exception("daypack: REPORT NOW section failed")
        parts.append(_hdr("REPORT NOW") + f"unavailable ({e})")

    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("""
                SELECT id, file_name, kind, note_date, char_count,
                       content_text
                FROM doc_uploads
                WHERE uploaded_at >= now() - make_interval(hours => %s)
                ORDER BY uploaded_at""", (LOOKBACK_HOURS,))
            rows = cur.fetchall()
        if not rows:
            parts.append(_hdr("UPLOADED DOCS") + "none in the last "
                         f"{LOOKBACK_HOURS}h")
        for rid, fn, kind, nd, chars, text in rows:
            label = (f"DOC {kind} · {nd or 'UNDATED ⚠'} · {fn or '?'} "
                     f"· {chars or 0:,} chars · id {rid}")
            if (chars or 0) > OMIT_OVER:
                parts.append(_hdr(label)
                             + f"OMITTED — {chars:,} chars would drown the "
                               f"pack; upload this file to the chat "
                               f"separately.")
                continue
            body = (text or "")
            if len(body) > DOC_CAP:
                body = (body[:DOC_CAP]
                        + f"\n… TRUNCATED at {DOC_CAP:,} of {len(text):,} "
                          f"chars (full text in doc_uploads id {rid})")
            parts.append(_hdr(label) + body)
    except Exception as e:
        log.exception("daypack: uploads section failed")
        parts.append(_hdr("UPLOADED DOCS") + f"unavailable ({e})")

    return "\n".join(parts)


def handle_daypack_command(text):
    """Telegram hook — owns exactly 'DAYPACK'. Returns a document reply
    dict; None to decline."""
    if not text or text.strip().upper() != SENTINEL:
        return None
    try:
        pack = build_daypack()
        try:
            from tools.report import store_report
            store_report(pack, "daypack")
        except Exception as e:
            log.warning("daypack store failed: %s", e)
        return {"document_name": f"daypack_{date.today()}.txt",
                "document_text": pack,
                "caption": (f"📦 DAYPACK {date.today()} — {len(pack):,} "
                            f"chars. Upload to your day chat alongside any "
                            f"omitted big files.")}
    except Exception as e:
        log.exception("DAYPACK failed")
        return f"🛑 DAYPACK error: {e}"
