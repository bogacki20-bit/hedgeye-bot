"""Position Monitors / Founder's Choice parser — sector publication log.

Subjects:
  "Position Monitors | <Sector> - Best Idea Longs & Shorts"
  "Founder's Choice: <Sector> - Best Idea Longs & Shorts"

These feeds are IMAGE-ONLY (the long/short tables are graphics:
"BEST IDEAS - LONGS ( VIEW LARGER IMAGE )"), so there is no ticker text to
extract. This parser records WHICH sector monitor published WHEN — a
publication log that completes typed-table coverage without inventing
ticker signals that aren't in the email text.

CLI: py parser_position_monitors.py [--message-id ID | --latest | --backfill | --probe ID]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from typing import Optional

log = logging.getLogger(__name__)

PM_RE = re.compile(r"Position\s+Monitors?\s*\|\s*(?P<sector>.+?)\s*-\s*Best\s+Idea",
                    re.I)
FC_RE = re.compile(r"Founder'?s?\s+Choice\s*:\s*(?P<sector>.+?)\s*-\s*Best\s+Idea",
                   re.I)
FEED_ID_RE = re.compile(r"feed_items/(\d+)")


def detect(subject: str) -> Optional[tuple[str, str]]:
    """Return (product, sector) or None."""
    s = re.sub(r"\s+", " ", subject or "").strip()
    m = PM_RE.search(s)
    if m:
        return "position_monitor", m.group("sector").strip()
    m = FC_RE.search(s)
    if m:
        return "founders_choice", m.group("sector").strip()
    return None


def is_position_monitor_subject(subject: str) -> bool:
    return detect(subject) is not None


def _strip_html(html: str) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["style", "script"]):
            t.decompose()
        text = soup.get_text(" ", strip=True)
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<style[^>]*>.*?</style>", "",
                       html, flags=re.S | re.I))
    return re.sub(r"\s+", " ", text).strip()


# ─────────────────────────── Persistence ───────────────────────────

def upsert_row(product: str, sector: str, signal_date: date,
               feed_item_id: int | None, message_id: str | None) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO hedgeye_position_monitors
                        (signal_date, sector, product, has_image_payload,
                         feed_item_id, source_email_id, parsed_at)
                    VALUES (%s,%s,%s,TRUE,%s,%s,NOW())
                    ON CONFLICT (signal_date, sector, product, source_email_id)
                    DO UPDATE SET feed_item_id=EXCLUDED.feed_item_id,
                        parsed_at=NOW()
                    """,
                    (signal_date, sector, product, feed_item_id, message_id),
                )
                conn.commit()
                return {"written": 1, "failed": 0}
            except Exception as e:
                conn.rollback()
                log.warning("PM upsert failed (%s/%s): %s", product, sector, e)
                return {"written": 0, "failed": 1}


def stamp_email_parsed(message_id: str, classified: str = "position_monitors") -> None:
    import db_pg
    try:
        db_pg.mark_email_classified(message_id, classified)
    except Exception as e:
        log.warning("stamp_email_parsed failed: %s", e)


# ─────────────────────────── Entry points ───────────────────────────

def process_email(message_id: str, *, dry_run: bool = False, fan_out: bool = True) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT subject, received_at, text_body, html_body "
                "FROM hedgeye_emails_raw WHERE message_id=%s", (message_id,),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "email not found"}
    subject, received_at, text_body, html_body = row
    det = detect(subject)
    if not det:
        return {"error": "subject not Position Monitor / Founder's Choice",
                "subject": subject}
    product, sector = det

    body = _strip_html(html_body or "") if not text_body else (text_body or "")
    # Sector comes from the subject (clean: "<Sector> - Best Idea ...").
    # A body-sector cross-check is intentionally NOT used — the body's first
    # "Hedgeye ..." token is the disclaimer, which over-captures.
    fm = FEED_ID_RE.search(body)
    feed_item_id = int(fm.group(1)) if fm else None
    snapshot_date = received_at.date() if received_at else date.today()

    summary = {
        "message_id": message_id, "subject": subject,
        "product": product, "sector": sector,
        "signal_date": str(snapshot_date), "rows_parsed": 1,
        "dry_run": dry_run,
        "note": "image-only feed — publication log row, no ticker text",
    }
    if dry_run:
        return summary

    summary["upsert"] = upsert_row(product, sector, snapshot_date,
                                   feed_item_id, message_id)
    stamp_email_parsed(message_id, "position_monitors")
    return summary


def process_one(message_id: str, *, fan_out: bool = True) -> dict:
    return process_email(message_id, fan_out=fan_out)


_SUBJ_SQL = ("(subject ILIKE 'Position Monitors |%' "
             "OR subject ILIKE 'Founder''s Choice:%' "
             "OR subject ILIKE 'Founders Choice:%')")


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "ORDER BY received_at DESC LIMIT 1")
            row = cur.fetchone()
    if not row:
        return {"error": "no Position Monitor emails"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "failed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "AND COALESCE(classified_as,'') NOT IN "
                "('position_monitors','position_monitors_parse_failed') "
                "ORDER BY received_at ASC")
            candidates = cur.fetchall()
    for (mid,) in candidates:
        try:
            r = process_email(mid, fan_out=fan_out)
            summary["failed" if r.get("error") else "processed"] += 1
        except Exception as e:
            summary["failed"] += 1
            summary["errors"].append({"mid": mid, "err": str(e)})
    return summary


def run_parser_cycle(batch_size: int = 100, fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "failed": 0}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "AND COALESCE(classified_as,'') NOT IN "
                "('position_monitors','position_monitors_parse_failed') "
                "ORDER BY received_at ASC LIMIT %s", (batch_size,))
            candidates = cur.fetchall()
    for (mid,) in candidates:
        try:
            r = process_email(mid, fan_out=fan_out)
            summary["failed" if r.get("error") else "processed"] += 1
        except Exception as e:
            summary["failed"] += 1
            log.warning("run_parser_cycle %s failed: %s", mid, e)
    return summary


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Position Monitors parser")
    ap.add_argument("--message-id")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--probe")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    if args.probe: r = process_email(args.probe, dry_run=True)
    elif args.message_id: r = process_email(args.message_id)
    elif args.latest: r = process_latest()
    elif args.backfill: r = backfill_all_unparsed()
    else: ap.print_help(); return 1
    print(json.dumps(r, indent=2, default=str), flush=True)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
