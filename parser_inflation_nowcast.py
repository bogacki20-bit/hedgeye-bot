"""Hedgeye Monthly Inflation Nowcast parser.

Real macro signal feeding Quad detection. The body states (in prose):
  - reported headline CPI y/y + sequential bps for the released month
  - the forward base-case nowcast (y/y + sequential bps) for next month
  - the next CPI release date
  - an accelerating / decelerating inflation regime

Best-effort numeric extraction; fields are NULL when a given edition
doesn't state them. Supersedes the metadata-only research_notes capture.

CLI: py parser_inflation_nowcast.py [--message-id ID | --latest | --backfill | --probe ID]
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

SUBJECT_RE = re.compile(r"Inflation\s+Nowcast", re.I)
CLASSIFIED = "inflation_nowcast"
_SUBJ_SQL = "subject ILIKE '%Inflation Nowcast%'"

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}

HEADLINE_RE = re.compile(
    r"Headline\s+CPI\s+inflation\s+measured\s+([+-]?\d+(?:\.\d+)?)\s*%\s*y/y"
    r"\s+in\s+([A-Z][a-z]+)", re.I)
HEADLINE_SEQ_RE = re.compile(
    r"(accelerat\w*|decelerat\w*|disinflat\w*)\s+(?:by\s+)?([+-]?\d+)\s*bps"
    r"\s+sequential", re.I)
# "May nowcast projects +44 bps of sequential acceleration to +4.06% y/y"
NOWCAST_FWD_RE = re.compile(
    r"([A-Z][a-z]+)\s+nowcast\s+projects\s+([+-]?\d+)\s*bps\s+of\s+sequential"
    r"\s+(accelerat\w*|decelerat\w*)[^.]*?to\s+\+?([0-9]+(?:\.\d+)?)\s*%\s*y/y",
    re.I)
# "base case nowcast of +3.62% y/y for April"
NOWCAST_BASE_RE = re.compile(
    r"base\s+case\s+nowcast\s+of\s+\+?([0-9]+(?:\.\d+)?)\s*%\s*y/y"
    r"(?:\s+for\s+([A-Z][a-z]+))?", re.I)
RELEASE_RE = re.compile(
    r"CPI\s+Release\s+Date:\s*([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?", re.I)


def is_inflation_nowcast_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def _signed(word: str, num: str):
    try:
        v = int(num)
    except ValueError:
        return None
    if re.match(r"decelerat|disinflat", word or "", re.I) and v > 0:
        v = -v
    return v


def _regime(body: str):
    acc = len(re.findall(r"accelerat", body or "", re.I))
    dec = len(re.findall(r"decelerat|disinflat", body or "", re.I))
    if acc and acc > dec:
        return "accelerating"
    if dec and dec > acc:
        return "decelerating"
    if acc or dec:
        return "mixed"
    return None


def parse_inflation_nowcast(body: str, year: int) -> dict:
    rec = {"report_period": None, "headline_cpi_yoy": None,
           "headline_seq_bps": None, "nowcast_period": None,
           "nowcast_yoy": None, "nowcast_seq_bps": None,
           "cpi_release_date": None, "regime": _regime(body),
           "quad": prc.quad(body)}

    m = HEADLINE_RE.search(body)
    if m:
        try:
            rec["headline_cpi_yoy"] = float(m.group(1))
        except ValueError:
            pass
        rec["report_period"] = m.group(2).title()

    m = HEADLINE_SEQ_RE.search(body)
    if m:
        rec["headline_seq_bps"] = _signed(m.group(1), m.group(2))

    m = NOWCAST_FWD_RE.search(body)
    if m:
        rec["nowcast_period"] = m.group(1).title()
        rec["nowcast_seq_bps"] = _signed(m.group(3), m.group(2))
        try:
            rec["nowcast_yoy"] = float(m.group(4))
        except ValueError:
            pass
    mb = NOWCAST_BASE_RE.search(body)
    if mb:
        if rec["nowcast_yoy"] is None:
            try:
                rec["nowcast_yoy"] = float(mb.group(1))
            except ValueError:
                pass
        if not rec["nowcast_period"] and mb.group(2):
            rec["nowcast_period"] = mb.group(2).title()

    m = RELEASE_RE.search(body)
    if m:
        mo = _MONTHS.get(m.group(1).lower())
        if mo:
            yr = year + 1 if mo < 3 else year   # Jan/Feb release rolls to next yr
            try:
                rec["cpi_release_date"] = date(yr, mo, int(m.group(2)))
            except ValueError:
                pass
    return rec


def upsert_row(sd, rec, feed_id, mid) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """INSERT INTO hedgeye_inflation_nowcast
                   (signal_date,report_period,headline_cpi_yoy,headline_seq_bps,
                    nowcast_period,nowcast_yoy,nowcast_seq_bps,cpi_release_date,
                    regime,quad,feed_item_id,source_email_id,parsed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                   ON CONFLICT (signal_date,source_email_id) DO UPDATE SET
                     report_period=EXCLUDED.report_period,
                     headline_cpi_yoy=EXCLUDED.headline_cpi_yoy,
                     headline_seq_bps=EXCLUDED.headline_seq_bps,
                     nowcast_period=EXCLUDED.nowcast_period,
                     nowcast_yoy=EXCLUDED.nowcast_yoy,
                     nowcast_seq_bps=EXCLUDED.nowcast_seq_bps,
                     cpi_release_date=EXCLUDED.cpi_release_date,
                     regime=EXCLUDED.regime, quad=EXCLUDED.quad,
                     feed_item_id=EXCLUDED.feed_item_id, parsed_at=NOW()""",
                (sd, rec["report_period"], rec["headline_cpi_yoy"],
                 rec["headline_seq_bps"], rec["nowcast_period"],
                 rec["nowcast_yoy"], rec["nowcast_seq_bps"],
                 rec["cpi_release_date"], rec["regime"], rec["quad"],
                 feed_id, mid),
            )
            conn.commit()
            return {"written": 1, "failed": 0}
        except Exception as e:
            conn.rollback()
            log.warning("inflation_nowcast upsert: %s", e)
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
    if not is_inflation_nowcast_subject(subject):
        return {"error": "subject not Inflation Nowcast", "subject": subject}
    body = prc.text_of(tb, hb)
    sd = received_at.date() if received_at else date.today()
    rec = parse_inflation_nowcast(body, sd.year)
    summ = {"message_id": message_id, "subject": subject,
            "signal_date": str(sd), "rows_parsed": 1,
            "parsed": rec, "dry_run": dry_run}
    if dry_run:
        return summ
    summ["upsert"] = upsert_row(sd, rec, prc.feed_item_id(body), message_id)
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
    ap = argparse.ArgumentParser(description="Hedgeye Inflation Nowcast parser")
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
