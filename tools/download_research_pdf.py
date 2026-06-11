"""Download + text-extract the slide deck linked from a Hedgeye research
note email, and surface the deck text into corpus_documents.

The research-note emails only carry a teaser; the actual Mid-Quarter Deck
lives behind a "CLICK HERE FOR THE … DECK" CTA whose href is a Hedgeye
click-tracker that 30x-redirects to the real PDF. Reaching it generally
requires the operator's logged-in session cookie (HEDGEYE_COOKIE).

Pipeline per note:
    full_report_url → GET (follow redirects, send cookie) → verify PDF →
    save under data/snapshots/hedgeye/research_notes/<date>/<id>_<slug>.pdf →
    pdfplumber text → corpus_documents(source='hedgeye_research_note_pdf').

Everything is best-effort: a 404 / auth wall / non-PDF response is logged
and returned as a status dict — it NEVER raises into the parser.

CLI:
    python -m tools.download_research_pdf --id 371
    python -m tools.download_research_pdf --backfill 10
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
_PDF_ROOT = _REPO / "data" / "snapshots" / "hedgeye" / "research_notes"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower())
    return s.strip("-")[:limit] or "deck"


def _looks_like_pdf(resp) -> bool:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "pdf" in ctype:
        return True
    # Some CDNs mislabel content-type; sniff the magic bytes.
    return resp.content[:5].startswith(b"%PDF")


def _fetch_pdf(url: str):
    """GET the URL following redirects with the operator cookie. Returns
    (status, response|None). status is 'ok' | 'not_pdf' | 'http_<code>' |
    'error:<msg>'."""
    import requests
    headers = {"User-Agent": _UA,
               "Accept": "application/pdf,*/*"}
    cookie = os.environ.get("HEDGEYE_COOKIE")
    if cookie:
        headers["Cookie"] = cookie
    try:
        resp = requests.get(url, headers=headers, allow_redirects=True,
                            timeout=45)
    except Exception as e:  # network / DNS / TLS
        return f"error:{e}", None
    if resp.status_code != 200:
        return f"http_{resp.status_code}", resp
    if not _looks_like_pdf(resp):
        return "not_pdf", resp
    return "ok", resp


def _extract_text(pdf_path: Path) -> tuple[str, int]:
    """Return (full_text, page_count). Empty text + 0 on failure."""
    try:
        import pdfplumber
    except ImportError:
        log.warning("pdfplumber not installed — deck text not extracted")
        return "", 0
    chunks: list[str] = []
    pages = 0
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            pages = len(pdf.pages)
            for pg in pdf.pages:
                chunks.append(pg.extract_text() or "")
    except Exception as e:
        log.warning("pdfplumber failed on %s: %s", pdf_path, e)
        return "", 0
    return "\n\n".join(c for c in chunks if c).strip(), pages


def _ingest_corpus(rn_id: int, subject: str, signal_date, full_text: str,
                   url: str, pdf_path: Path, pages: int) -> str:
    """Insert the deck text as a corpus_documents row
    (source='hedgeye_research_note_pdf'). Idempotent on the natural key."""
    import db_pg
    import psycopg2.extras
    md = {
        "research_note_id": rn_id,
        "source_url":       url,
        "pdf_path":         str(pdf_path),
        "pages":            pages,
        "subject":          subject,
    }
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO corpus_documents
                   (source, source_date, source_ref, document_type,
                    page_or_segment, title, full_text, metadata)
               VALUES ('hedgeye_research_note_pdf', %s, %s,
                       'slide_deck_text', NULL, %s, %s, %s)
               ON CONFLICT (source, COALESCE(source_ref, ''), source_date,
                            COALESCE(page_or_segment, -1))
               DO UPDATE SET title=EXCLUDED.title,
                             full_text=EXCLUDED.full_text,
                             metadata=EXCLUDED.metadata,
                             captured_at=NOW()
               RETURNING (xmax = 0) AS inserted""",
            (signal_date, str(rn_id), subject,
             full_text or subject, psycopg2.extras.Json(md)),
        )
        inserted = bool(cur.fetchone()[0])
        conn.commit()
    return "inserted" if inserted else "updated"


def download_and_ingest(rn_id: int, url: str, subject: str,
                        signal_date) -> dict:
    """Download the deck PDF for one research note and ingest its text.

    Best-effort: returns a status dict, never raises. Statuses:
      'ingested'   — PDF fetched, saved, text extracted, corpus row written
      'saved_no_text' — PDF saved but pdfplumber yielded no text
      'unreachable'   — non-200 / not a PDF (auth wall, 404, login redirect)
      'no_url'        — nothing to download
    """
    if not url:
        return {"status": "no_url"}
    if isinstance(signal_date, str):
        try:
            signal_date = date.fromisoformat(signal_date[:10])
        except ValueError:
            signal_date = date.today()

    status, resp = _fetch_pdf(url)
    if status != "ok":
        log.info("research-note PDF unreachable (id=%s, %s): %s",
                 rn_id, status, url[:80])
        return {"status": "unreachable", "reason": status, "id": rn_id}

    day_dir = _PDF_ROOT / signal_date.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = day_dir / f"{rn_id}_{_slug(subject)}.pdf"
    try:
        pdf_path.write_bytes(resp.content)
    except Exception as e:
        log.warning("could not save deck PDF (id=%s): %s", rn_id, e)
        return {"status": "error", "reason": str(e), "id": rn_id}

    full_text, pages = _extract_text(pdf_path)
    if not full_text:
        return {"status": "saved_no_text", "id": rn_id,
                "pdf_path": str(pdf_path), "pages": pages}

    corpus = _ingest_corpus(rn_id, subject, signal_date, full_text,
                            url, pdf_path, pages)
    return {"status": "ingested", "id": rn_id, "pages": pages,
            "chars": len(full_text), "pdf_path": str(pdf_path),
            "corpus": corpus}


def _discover_url(message_id: str) -> str | None:
    """Re-derive the deck URL from a note's email HTML (for backfill of
    rows parsed before full_report_url existed)."""
    import db_pg
    import parser_research_common as prc
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT html_body FROM hedgeye_emails_raw WHERE "
                    "message_id=%s", (message_id,))
        r = cur.fetchone()
    return prc.deck_pdf_url(r[0]) if r and r[0] else None


def backfill(limit: int = 10) -> dict:
    """Download + ingest decks for the most recent `limit` research notes
    that have (or can discover) a deck URL. Persists any newly-discovered
    URL back onto hedgeye_research_notes.full_report_url."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, source_email_id, title, signal_date, full_report_url
                 FROM hedgeye_research_notes
                ORDER BY signal_date DESC, id DESC
                LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()

    out = {"considered": len(rows), "attempted": 0, "ingested": 0,
           "unreachable": 0, "no_url": 0, "results": []}
    for rn_id, mid, subject, sd, url in rows:
        if not url and mid:
            url = _discover_url(mid)
            if url:
                with db_pg.get_conn() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE hedgeye_research_notes "
                                "SET full_report_url=%s WHERE id=%s",
                                (url, rn_id))
                    conn.commit()
        if not url:
            out["no_url"] += 1
            out["results"].append({"id": rn_id, "status": "no_url"})
            continue
        out["attempted"] += 1
        res = download_and_ingest(rn_id, url, subject, sd)
        if res.get("status") == "ingested":
            out["ingested"] += 1
        elif res.get("status") == "unreachable":
            out["unreachable"] += 1
        out["results"].append(res)
    return out


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.download_research_pdf")
    ap.add_argument("--id", type=int, help="research_note id to download")
    ap.add_argument("--backfill", type=int, metavar="N",
                    help="download decks for the N most recent notes")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.backfill:
        print(json.dumps(backfill(a.backfill), indent=2, default=str))
        return 0
    if a.id:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, source_email_id, title, signal_date,
                          full_report_url
                     FROM hedgeye_research_notes WHERE id=%s""", (a.id,))
            r = cur.fetchone()
        if not r:
            print(json.dumps({"error": "not found", "id": a.id}))
            return 1
        rn_id, mid, subject, sd, url = r
        if not url and mid:
            url = _discover_url(mid)
        print(json.dumps(download_and_ingest(rn_id, url, subject, sd),
                         indent=2, default=str))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
