"""The Call @ Hedgeye parser — daily analyst call positions.

Subjects: "The Call @ Hedgeye | Replay & Summary | M/D", "The Call Recap:
Financials | ...". Body has a structured HEDGEYE POSITIONS block:
  "HEDGEYE POSITIONS ... LONGS: CZR, VIK, MGM ... SHORTS: ..."

CLI: py parser_the_call.py [--message-id ID | --latest | --backfill | --probe ID]
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

SUBJECT_RE = re.compile(r"^\s*The\s+Call\b", re.I)
CLASSIFIED = "the_call"
_SUBJ_SQL = "subject ILIKE 'The Call %'"


def is_the_call_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def parse_the_call(body: str) -> list[dict]:
    # Anchor to the POSITIONS block so prose tickers don't leak in.
    m = re.search(r"HEDGEYE\s+POSITIONS(?P<blk>.+?)(?:Please\s+visit|"
                  r"\(\s*VIEW|TL;?DR|$)", body or "", re.I | re.S)
    scope = m.group("blk") if m else (body or "")
    return prc.side_blocks(scope)


def upsert_rows(rows, signal_date, feed_id, mid) -> dict:
    import db_pg
    w = f = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            try:
                cur.execute(
                    """INSERT INTO hedgeye_the_call
                       (signal_date,ticker,side,source_email_id,feed_item_id,parsed_at)
                       VALUES (%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT (signal_date,ticker,side,source_email_id)
                       DO UPDATE SET feed_item_id=EXCLUDED.feed_item_id,
                                     parsed_at=NOW()""",
                    (signal_date, r["ticker"], r["side"], mid, feed_id),
                )
                w += 1
            except Exception as e:
                log.warning("the_call upsert %s: %s", r.get("ticker"), e)
                f += 1
        conn.commit()
    return {"written": w, "failed": f}


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
                [r["ticker"]], source="the_call", position=r["side"],
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
    if not is_the_call_subject(subject):
        return {"error": "subject not The Call", "subject": subject}
    body = prc.text_of(tb, hb)
    rows = parse_the_call(body)
    sd = received_at.date() if received_at else date.today()
    summ = {"message_id": message_id, "subject": subject,
            "signal_date": str(sd), "rows_parsed": len(rows),
            "rows": rows, "dry_run": dry_run}
    if not rows:
        # No POSITIONS block (older/teaser editions) — still a valid Call
        # email; classify (not a failure) so it isn't reprocessed forever.
        if not dry_run:
            _stamp(message_id)
        summ["no_positions"] = True
        return summ
    if dry_run:
        return summ
    summ["upsert"] = upsert_rows(rows, sd, prc.feed_item_id(body), message_id)
    summ["noted_in_inventory"] = _note(rows, message_id)
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
    ap = argparse.ArgumentParser(description="The Call @ Hedgeye parser")
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
