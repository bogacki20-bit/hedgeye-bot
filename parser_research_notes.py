"""Consolidated parser for the Hedgeye narrative/macro research family.

One typed table (hedgeye_research_notes) with a product discriminator.
These products are prose / data commentary — not ticker rosters — so the
honest typed capture is: product, title, quad regime, author handles,
parenthetical tickers mentioned, body length, feed id. One row per email.

Products: Early Look, Market Situation Report, Monthly Inflation Nowcast,
Fed H.8, Macro Week Summary Notes, JT Taylor Geopolitical, Quads/GIP
Update, Hedgeye QIO, Monthly Commentary, Senior Loan Officer Survey,
Weekly Position Monitor, Updated Levels, Trend & Tail Levels, KM Top 3.

CLI: py parser_research_notes.py [--message-id ID | --latest | --backfill | --probe ID]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date

import parser_research_common as prc

log = logging.getLogger(__name__)

CLASSIFIED = "research_note"

# (compiled subject regex, product) — order matters (first match wins).
_PRODUCTS = [
    (re.compile(r"^\s*EARLY\s+LOOK\b", re.I),               "early_look"),
    (re.compile(r"^\s*MARKET\s+SITUATION\s+REPORT\b", re.I), "market_situation_report"),
    # Inflation Nowcast now has its own dedicated parser/table (parser_inflation_nowcast).
    (re.compile(r"Federal\s+Reserve\s+Weekly\s+H", re.I),   "fed_h8"),
    (re.compile(r"^\s*Macro\s+Week\s+Summary\s+Notes", re.I), "macro_week"),
    (re.compile(r"^\s*JT\s+TAYLOR\b", re.I),                "jt_taylor"),
    (re.compile(r"^\s*Quads?\s*/\s*GIP\s+Update", re.I),    "quads_gip"),
    (re.compile(r"^\s*Hedgeye\s+QIO\b", re.I),              "hedgeye_qio"),
    (re.compile(r"^\s*Monthly\s+Commentary\b", re.I),       "monthly_commentary"),
    (re.compile(r"Senior\s+Loan\s+Officer", re.I),          "senior_loan_officer"),
    (re.compile(r"^\s*Weekly\s+Position\s+Monitor", re.I),  "weekly_position_monitor"),
    (re.compile(r"^\s*Updated\s+Levels\b", re.I),           "updated_levels"),
    (re.compile(r"^\s*Trend\s*&\s*Tail\s+Levels", re.I),    "trend_tail_levels"),
    (re.compile(r"Top\s+3\s+Things", re.I),                 "km_top3"),
]

# SQL OR-clause matching the same set (substring, case-insensitive).
_SUBJ_SQL = (
    "(subject ILIKE 'EARLY LOOK%' "
    "OR subject ILIKE 'MARKET SITUATION REPORT%' "
    "OR subject ILIKE '%Federal Reserve Weekly H%' "
    "OR subject ILIKE 'Macro Week Summary Notes%' "
    "OR subject ILIKE 'JT TAYLOR%' "
    "OR subject ILIKE 'Quads/GIP Update%' OR subject ILIKE 'Quad/GIP Update%' "
    "OR subject ILIKE 'Hedgeye QIO%' "
    "OR subject ILIKE 'Monthly Commentary%' "
    "OR subject ILIKE '%Senior Loan Officer%' "
    "OR subject ILIKE 'Weekly Position Monitor%' "
    "OR subject ILIKE 'Updated Levels%' "
    "OR subject ILIKE 'Trend & Tail Levels%' "
    "OR subject ILIKE '%Top 3 Things%')"
)


def detect_product(subject: str):
    s = subject or ""
    for rx, prod in _PRODUCTS:
        if rx.search(s):
            return prod
    return None


def is_research_note_subject(subject: str) -> bool:
    return detect_product(subject) is not None


def parse_research_note(subject: str, body: str, html_body: str | None = None,
                        ref_date: date | None = None) -> dict:
    # `quad` (legacy) holds the first Quad mention — kept for back-compat.
    # `tilt_target_quads` holds the DESTINATION of any regime tilt (the
    # value the bot must actually act on); effective_from_date is the
    # forward-switch date for the monthly axis (NULL = apply immediately).
    tilt = prc.tilt_target_quads(subject, body, ref_date)
    eff_from = tilt.get("monthly_effective_from") if tilt else None
    return {
        "title": re.sub(r"\s+", " ", subject or "").strip()[:300],
        "quad": prc.quad(body),
        "tilt_target_quads": tilt,
        "effective_from_date": eff_from,
        "full_report_url": prc.deck_pdf_url(html_body),
        "authors": ",".join(prc.authors(body)) or None,
        "tickers_mentioned": ",".join(
            prc.clean_tickers(body, paren_only=True, limit=40)) or None,
        "body_chars": len(body or ""),
        "feed_item_id": prc.feed_item_id(body),
    }


def upsert_row(product, sd, rec, mid) -> dict:
    import db_pg
    import psycopg2.extras
    tilt = rec.get("tilt_target_quads")
    tilt_json = psycopg2.extras.Json(tilt) if tilt is not None else None
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """INSERT INTO hedgeye_research_notes
                   (signal_date,product,title,quad,tilt_target_quads,
                    effective_from_date,full_report_url,authors,
                    tickers_mentioned,body_chars,feed_item_id,
                    source_email_id,parsed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                   ON CONFLICT (product,source_email_id) DO UPDATE SET
                     title=EXCLUDED.title, quad=EXCLUDED.quad,
                     tilt_target_quads=EXCLUDED.tilt_target_quads,
                     effective_from_date=EXCLUDED.effective_from_date,
                     full_report_url=COALESCE(EXCLUDED.full_report_url,
                                              hedgeye_research_notes.full_report_url),
                     authors=EXCLUDED.authors,
                     tickers_mentioned=EXCLUDED.tickers_mentioned,
                     body_chars=EXCLUDED.body_chars,
                     feed_item_id=EXCLUDED.feed_item_id, parsed_at=NOW()
                   RETURNING id""",
                (sd, product, rec["title"], rec["quad"], tilt_json,
                 rec["effective_from_date"], rec["full_report_url"],
                 rec["authors"], rec["tickers_mentioned"], rec["body_chars"],
                 rec["feed_item_id"], mid),
            )
            row_id = cur.fetchone()[0]
            conn.commit()
            return {"written": 1, "failed": 0, "id": row_id}
        except Exception as e:
            conn.rollback()
            log.warning("research_notes upsert (%s): %s", product, e)
            return {"written": 0, "failed": 1}


def _stamp(mid, c=CLASSIFIED):
    import db_pg
    try:
        db_pg.mark_email_classified(mid, c)
    except Exception as e:
        log.warning("stamp failed: %s", e)


def sync_to_corpus(product, sd, rec, body, mid) -> dict:
    """Mirror a research note into corpus_documents so the RAG / ML layer
    can see it (these rows were previously invisible to the corpus).

    source='hedgeye_research_note', source_ref=message_id (the natural key
    for idempotency via the existing corpus_documents_unique index).
    full_text = the email body; metadata carries the structured product /
    quad / tilt fields so ML can pivot without re-parsing the prose."""
    import db_pg
    import psycopg2.extras
    md = {
        "product":             product,
        "quad":                rec.get("quad"),
        "tilt_target_quads":   rec.get("tilt_target_quads"),
        "effective_from_date": rec.get("effective_from_date"),
        "classification":      CLASSIFIED,
        "authors":             rec.get("authors"),
        "tickers_mentioned":   rec.get("tickers_mentioned"),
        "full_report_url":     rec.get("full_report_url"),
        "subject":             rec.get("title"),
    }
    full_text = (body or "").strip() or (rec.get("title") or "")
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO corpus_documents
                       (source, source_date, source_ref, document_type,
                        page_or_segment, title, full_text, metadata)
                   VALUES ('hedgeye_research_note', %s, %s,
                           'research_note_email', NULL, %s, %s, %s)
                   ON CONFLICT (source, COALESCE(source_ref, ''), source_date,
                                COALESCE(page_or_segment, -1))
                   DO UPDATE SET title=EXCLUDED.title,
                                 full_text=EXCLUDED.full_text,
                                 metadata=EXCLUDED.metadata,
                                 captured_at=NOW()
                   RETURNING (xmax = 0) AS inserted""",
                (sd, mid, rec.get("title"), full_text,
                 psycopg2.extras.Json(md)),
            )
            inserted = bool(cur.fetchone()[0])
            conn.commit()
            return {"corpus": "inserted" if inserted else "updated"}
    except Exception as e:
        log.warning("research_notes corpus sync (%s): %s", product, e)
        return {"corpus": "failed", "error": str(e)}


def process_email(message_id: str, *, dry_run=False, fan_out=False,
                  apply_regime=True) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT subject,received_at,text_body,html_body "
                    "FROM hedgeye_emails_raw WHERE message_id=%s",
                    (message_id,))
        row = cur.fetchone()
    if not row:
        return {"error": "email not found"}
    subject, received_at, tb, hb = row
    product = detect_product(subject)
    if not product:
        return {"error": "subject not a research note", "subject": subject}
    body = prc.text_of(tb, hb)
    sd = received_at.date() if received_at else date.today()
    rec = parse_research_note(subject, body, html_body=hb, ref_date=sd)
    summ = {"message_id": message_id, "subject": subject,
            "product": product, "signal_date": str(sd),
            "rows_parsed": 1, "parsed": rec, "dry_run": dry_run}
    if dry_run:
        return summ
    summ["upsert"] = upsert_row(product, sd, rec, message_id)
    rn_id = summ["upsert"].get("id")
    # Fix 3 — surface every research note into corpus_documents (RAG/ML).
    summ["corpus"] = sync_to_corpus(product, sd, rec, body, message_id)
    # Fix 2 — a tilt target USED to auto-advance the live regime. That is now
    # GATED (2026-06-29, Hedgeye email-only compliance): the dashboard scrape
    # that silently corrected mis-parsed email quads is gone, so an unguarded
    # auto-apply would move the canonical regime off a daily Early Look tone
    # with nothing to undo it. Default: NEVER auto-write. A Quads/GIP DECK tilt
    # PROPOSES a flip (operator confirms via QUAD:); daily tones feed the
    # early-warning detector. The QUAD: bridge stays the sole canonical writer.
    # Re-enable the legacy auto-apply only with QUAD_AUTO_APPLY=1.
    if apply_regime and rec.get("tilt_target_quads"):
        try:
            if os.getenv("QUAD_AUTO_APPLY") == "1":
                from tools.quad_regime import apply_research_note_tilts
                summ["regime"] = apply_research_note_tilts()
            else:
                from tools.quad_detector import on_research_note
                summ["regime"] = on_research_note(
                    product, rec.get("tilt_target_quads"), sd,
                    message_id=message_id)
        except Exception as e:
            log.warning("regime update from research note failed: %s", e)
            summ["regime"] = {"error": str(e)}
    # Slide-deck PDF download REMOVED 2026-06-29 (Hedgeye email-only compliance):
    # it fetched the deck from a hedgeye.com click-tracker behind the login. The
    # email itself is still fully parsed/stored above; full_report_url is still
    # captured as a string (no fetch). Decks already ingested into
    # corpus_documents (source='hedgeye_research_note_pdf') are unaffected.
    _stamp(message_id)
    return summ


def process_one(message_id: str, *, fan_out=False) -> dict:
    return process_email(message_id, fan_out=fan_out)


def process_latest(dry_run=False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT message_id FROM hedgeye_emails_raw WHERE "
                    f"{_SUBJ_SQL} ORDER BY received_at DESC LIMIT 1")
        r = cur.fetchone()
    return process_email(r[0], dry_run=dry_run) if r else {"error": "none"}


def _candidates(limit=None):
    import db_pg
    q = (f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
         f"AND COALESCE(classified_as,'') NOT IN "
         f"('{CLASSIFIED}','{CLASSIFIED}_parse_failed') "
         f"ORDER BY received_at ASC")
    if limit:
        q += f" LIMIT {int(limit)}"
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(q)
        return [r[0] for r in cur.fetchall()]


def backfill_all_unparsed(fan_out=False) -> dict:
    s = {"processed": 0, "failed": 0, "errors": []}
    for mid in _candidates():
        try:
            r = process_email(mid, fan_out=fan_out)
            s["failed" if r.get("error") else "processed"] += 1
        except Exception as e:
            s["failed"] += 1
            s["errors"].append({"mid": mid, "err": str(e)})
    return s


def backfill_corpus(limit=None) -> dict:
    """Mirror every existing hedgeye_research_notes row into corpus_documents
    so historical notes are visible to RAG/ML (they were never synced before
    Fix 3). Idempotent — re-running refreshes rows in place. Re-derives the
    structured tilt fields from the live email body so older rows (parsed
    before tilt_target_quads existed) get the destination Quad too, and
    persists those back onto the typed row."""
    import db_pg
    q = ("SELECT r.id, r.source_email_id, r.product, r.signal_date, "
         "e.subject, e.text_body, e.html_body "
         "FROM hedgeye_research_notes r "
         "JOIN hedgeye_emails_raw e ON e.message_id = r.source_email_id "
         "ORDER BY r.signal_date ASC, r.id ASC")
    if limit:
        q += f" LIMIT {int(limit)}"
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()

    s = {"total": len(rows), "inserted": 0, "updated": 0, "failed": 0}
    for rn_id, mid, product, sd, subject, tb, hb in rows:
        try:
            body = prc.text_of(tb, hb)
            rec = parse_research_note(subject, body, html_body=hb, ref_date=sd)
            # Refresh the typed row's structured columns too (back-fills the
            # tilt fields on rows parsed before they existed).
            upsert_row(product, sd, rec, mid)
            r = sync_to_corpus(product, sd, rec, body, mid)
            if r.get("corpus") == "inserted":
                s["inserted"] += 1
            elif r.get("corpus") == "updated":
                s["updated"] += 1
            else:
                s["failed"] += 1
        except Exception as e:
            s["failed"] += 1
            log.warning("corpus backfill id=%s: %s", rn_id, e)
    return s


def run_parser_cycle(batch_size=100, fan_out=False) -> dict:
    s = {"processed": 0, "failed": 0}
    for mid in _candidates(batch_size):
        try:
            r = process_email(mid, fan_out=fan_out)
            s["failed" if r.get("error") else "processed"] += 1
        except Exception as e:
            s["failed"] += 1
            log.warning("cycle %s: %s", mid, e)
    return s


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Hedgeye research-notes parser")
    ap.add_argument("--message-id")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--backfill-corpus", action="store_true",
                    help="mirror every existing research note into "
                         "corpus_documents (Fix 3 backfill)")
    ap.add_argument("--probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    if a.probe: r = process_email(a.probe, dry_run=True)
    elif a.message_id: r = process_email(a.message_id)
    elif a.latest: r = process_latest()
    elif a.backfill: r = backfill_all_unparsed()
    elif a.backfill_corpus: r = backfill_corpus()
    else: ap.print_help(); return 1
    print(json.dumps(r, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
