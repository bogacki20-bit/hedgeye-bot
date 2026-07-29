"""Keith's Signal Longs/Shorts parser.

Subject: "Keith's Signal Longs/Shorts". Body:
  "Keith's Signal Strength List  LONGS: V, XYZ, AFRM ...
   SHORTS : MA, ADYEY, FISV ..."
-> N (ticker, side) rows.

CLI: py parser_keiths_signals.py [--message-id ID | --latest | --backfill | --probe ID]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date

import parser_research_common as prc

log = logging.getLogger(__name__)

SUBJECT_RE = re.compile(r"Keith'?s\s+Signal\s+Longs?\s*/\s*Shorts?", re.I)
CLASSIFIED = "keiths_signals"
_SUBJ_SQL = "subject ILIKE %s"
_SUBJ_LIKE = "Keith%Signal Longs%"


def is_keiths_signals_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def parse_keiths_signals(body: str) -> list[dict]:
    return prc.side_blocks(body or "")


def upsert_rows(rows, signal_date, feed_id, mid) -> dict:
    import db_pg
    w = f = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            try:
                cur.execute(
                    """INSERT INTO hedgeye_keiths_signals
                       (signal_date,ticker,side,source_email_id,feed_item_id,parsed_at)
                       VALUES (%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT (signal_date,ticker,side,source_email_id)
                       DO UPDATE SET feed_item_id=EXCLUDED.feed_item_id,
                                     parsed_at=NOW()""",
                    (signal_date, r["ticker"], r["side"], mid, feed_id),
                )
                w += 1
            except Exception as e:
                log.warning("keiths upsert %s: %s", r.get("ticker"), e)
                f += 1
        conn.commit()
    return {"written": w, "failed": f}


def sync_to_corpus(message_id, subject, received_at, body, rows) -> bool:
    """Mirror the Keith's Signal email into corpus_documents.

    Previously this parser wrote ONLY the structured rows to
    hedgeye_keiths_signals, so active_slice / SCREEN saw the sides but the RAG
    layer and decision_engine._get_corpus_snippets saw nothing — a "why is AXP a
    short this week" lookup returned no narrative at all. parser_research_notes
    and parser_portfolio_solutions both mirror their publications; this one did
    not.

    Uses db_pg._insert_corpus_document, the shared writer. captured_dt is the
    email's received_at, so source_ref is that timestamp and a re-parse of the
    same email UPDATEs in place rather than duplicating.
    """
    import db_pg
    if received_at is None:
        log.warning("keiths corpus: no received_at for %s; skipping", message_id)
        return False
    longs = sorted({r["ticker"] for r in rows
                    if "long" in (r.get("side") or "").lower()})
    shorts = sorted({r["ticker"] for r in rows
                     if "short" in (r.get("side") or "").lower()})
    md = {
        "product":        "Keith's Signal Longs/Shorts",
        "classification": CLASSIFIED,
        "message_id":     message_id,
        "subject":        subject,
        "signal_date":    str(received_at.date()),
        "longs":          longs,
        "shorts":         shorts,
        "n_long":         len(longs),
        "n_short":        len(shorts),
    }
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            inserted = db_pg._insert_corpus_document(
                cur,
                source="keiths_signals",
                captured_dt=received_at,
                title=subject or "Keith's Signal Longs/Shorts",
                raw_text=body,
                metadata=md,
                document_type="keiths_signals_email",
            )
            conn.commit()
        return bool(inserted)
    except Exception as e:
        # Never fail the parse over the corpus mirror — the structured rows
        # (which SCREEN and active_slice depend on) are already committed.
        log.warning("keiths_signals corpus sync failed: %s", e)
        return False


def _stamp(mid, c=CLASSIFIED):
    import db_pg
    try:
        db_pg.mark_email_classified(mid, c)
    except Exception as e:
        log.warning("stamp failed: %s", e)


def _note(rows, mid):
    try:
        import ticker_inventory
        n = 0
        for r in rows:
            n += ticker_inventory.note_tickers(
                [r["ticker"]], source="keiths_signals", position=r["side"],
                message_id=mid).get("noted", 0)
        return n
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def process_email(message_id: str, *, dry_run=False, fan_out=False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT subject,received_at,text_body,html_body "
                    "FROM hedgeye_emails_raw WHERE message_id=%s",
                    (message_id,))
        row = cur.fetchone()
    if not row:
        return {"error": "email not found"}
    subject, received_at, tb, hb = row
    if not is_keiths_signals_subject(subject):
        return {"error": "subject not Keith's Signals", "subject": subject}
    body = prc.text_of(tb, hb)
    rows = parse_keiths_signals(body)
    sd = received_at.date() if received_at else date.today()
    summ = {"message_id": message_id, "subject": subject,
            "signal_date": str(sd), "rows_parsed": len(rows),
            "rows": rows, "dry_run": dry_run}
    if not rows:
        if not dry_run:
            _stamp(message_id, CLASSIFIED + "_parse_failed")
        summ["error"] = "no LONGS/SHORTS block"
        return summ
    if dry_run:
        return summ
    summ["upsert"] = upsert_rows(rows, sd, prc.feed_item_id(body), message_id)
    summ["noted_in_inventory"] = _note(rows, message_id)
    summ["corpus"] = sync_to_corpus(message_id, subject, received_at, body, rows)
    _stamp(message_id)
    return summ


def process_one(message_id: str, *, fan_out=False) -> dict:
    return process_email(message_id, fan_out=fan_out)


def process_latest(dry_run=False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT message_id FROM hedgeye_emails_raw WHERE "
                    f"{_SUBJ_SQL} ORDER BY received_at DESC LIMIT 1",
                    (_SUBJ_LIKE,))
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
        cur.execute(q, (_SUBJ_LIKE,))
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
    ap = argparse.ArgumentParser(description="Keith's Signal Longs/Shorts parser")
    ap.add_argument("--message-id")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    if a.probe: r = process_email(a.probe, dry_run=True)
    elif a.message_id: r = process_email(a.message_id)
    elif a.latest: r = process_latest()
    elif a.backfill: r = backfill_all_unparsed()
    else: ap.print_help(); return 1
    print(json.dumps(r, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
