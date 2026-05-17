"""Model Portfolio Changes parser — publication log.

The changes themselves ship as a PDF/image; the email text only gives the
effective date. Subject: "Model Portfolio Changes | Effective 5/11/2026"
(or "-- Effective 3/30/26"); body: "...go into effect using closing
prices on Monday, May 11, 2026". One publication-log row per email.

CLI: py parser_model_portfolio.py [--message-id ID | --latest | --backfill | --probe ID]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime

import parser_research_common as prc

log = logging.getLogger(__name__)

SUBJECT_RE = re.compile(r"^\s*Model\s+Portfolio\s+Changes\b", re.I)
CLASSIFIED = "model_portfolio"
_SUBJ_SQL = "subject ILIKE 'Model Portfolio Changes%'"

SUBJ_EFF_RE = re.compile(r"Effective\s+(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)
BODY_EFF_RE = re.compile(
    r"closing\s+prices\s+on\s+\w+,?\s+([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
    re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}


def is_model_portfolio_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def _effective_date(subject: str, body: str):
    m = SUBJ_EFF_RE.search(subject or "")
    if m:
        mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if yr < 100:
            yr += 2000
        try:
            return date(yr, mo, da)
        except ValueError:
            pass
    m = BODY_EFF_RE.search(body or "")
    if m:
        mo = _MONTHS.get(m.group(1).lower())
        if mo:
            try:
                return date(int(m.group(3)), mo, int(m.group(2)))
            except ValueError:
                pass
    return None


def upsert_row(signal_date, eff, feed_id, mid) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """INSERT INTO hedgeye_model_portfolio
                   (signal_date,effective_date,has_pdf_payload,feed_item_id,
                    source_email_id,parsed_at)
                   VALUES (%s,%s,TRUE,%s,%s,NOW())
                   ON CONFLICT (signal_date,source_email_id)
                   DO UPDATE SET effective_date=EXCLUDED.effective_date,
                                 feed_item_id=EXCLUDED.feed_item_id,
                                 parsed_at=NOW()""",
                (signal_date, eff, feed_id, mid),
            )
            conn.commit()
            return {"written": 1, "failed": 0}
        except Exception as e:
            conn.rollback()
            log.warning("model_portfolio upsert: %s", e)
            return {"written": 0, "failed": 1}


def _stamp(mid, c=CLASSIFIED):
    import db_pg
    try:
        db_pg.mark_email_classified(mid, c)
    except Exception as e:
        log.warning("stamp failed: %s", e)


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
    if not is_model_portfolio_subject(subject):
        return {"error": "subject not Model Portfolio", "subject": subject}
    body = prc.text_of(tb, hb)
    sd = received_at.date() if received_at else date.today()
    eff = _effective_date(subject, body)
    summ = {"message_id": message_id, "subject": subject,
            "signal_date": str(sd), "effective_date": str(eff),
            "rows_parsed": 1, "dry_run": dry_run,
            "note": "PDF/image-only changes — publication-log row"}
    if dry_run:
        return summ
    summ["upsert"] = upsert_row(sd, eff, prc.feed_item_id(body), message_id)
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
    ap = argparse.ArgumentParser(description="Model Portfolio Changes parser")
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
