"""Investing Ideas parser — Hedgeye's Top Stock Picks leaderboard.

Subject: "Top Stock Picks | The Leaderboard: Top 21 Long Ideas"
Body has two blocks:
  1) DATES ADDED list: "VMC: 1/7/2025, SITM: 4/11/2025, ..."
  2) RANK / TICKER / ANALYST / % CHANGE / DATE ADDED & REMOVED table

This parser captures (rank, ticker, analyst, pct_change, date_added) into
hedgeye_investing_ideas with (snapshot_date, ticker) as PK.

CLI:
  py parser_investing_ideas.py --message-id <id>
  py parser_investing_ideas.py --latest
  py parser_investing_ideas.py --backfill
  py parser_investing_ideas.py --probe <id>
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime
from typing import Optional

log = logging.getLogger(__name__)


SUBJECT_RE = re.compile(r"Top\s+Stock\s+Picks.*Leaderboard", re.I)


def is_investing_ideas_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["style", "script"]):
            t.decompose()
        return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()


# Row pattern after "RANK / TICKER / ANALYST / % CHANGE / DATE ADDED":
#   #1 EAT Howard Penney 102.4% 9/10/24 - 1/6/25 Get Report
# The row layout per the captured excerpt is multi-line:
#   #1
#   EAT
#   Howard Penney
#   102.4%
#   9/10/24 - 1/6/25
#   Get Report
ROW_RE = re.compile(
    r"#(\d+)\s+([A-Z]{1,5})\s+([A-Za-z][A-Za-z\s]+?)\s+"
    r"(-?\d+\.\d+)%\s+"
    r"(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})",
    re.S,
)


def parse_body(html_body: str) -> list[dict]:
    text = _strip_html(html_body)
    rows: list[dict] = []
    for m in ROW_RE.finditer(text):
        rank = int(m.group(1))
        ticker = m.group(2)
        analyst = m.group(3).strip()
        try:
            pct = float(m.group(4))
        except ValueError:
            pct = None
        try:
            d_added = datetime.strptime(
                m.group(5),
                "%m/%d/%y" if len(m.group(5).split("/")[-1]) == 2 else "%m/%d/%Y"
            ).date()
        except ValueError:
            d_added = None
        rows.append({
            "rank":       rank,
            "ticker":     ticker,
            "analyst":    analyst,
            "pct_change": pct,
            "date_added": d_added,
        })
    return rows


# ─────────────────────────── Persistence ───────────────────────────

def upsert_rows(rows: list[dict], snapshot_date: date, message_id: str | None) -> dict:
    import db_pg
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_investing_ideas
                            (snapshot_date, rank, ticker, analyst,
                             pct_change, date_added,
                             source_email_id, parsed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (snapshot_date, ticker) DO UPDATE
                            SET rank            = EXCLUDED.rank,
                                analyst         = EXCLUDED.analyst,
                                pct_change      = EXCLUDED.pct_change,
                                date_added      = EXCLUDED.date_added,
                                source_email_id = EXCLUDED.source_email_id,
                                parsed_at       = NOW()
                        """,
                        (snapshot_date, r["rank"], r["ticker"], r["analyst"],
                         r["pct_change"], r["date_added"], message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("II upsert failed for %s: %s", r["ticker"], e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str) -> None:
    import db_pg
    try:
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE hedgeye_emails_raw "
                    "SET classified_as='investing_ideas', parsed_at=NOW() "
                    "WHERE message_id=%s",
                    (message_id,),
                )
            conn.commit()
    except Exception as e:
        log.warning("stamp_email_parsed failed: %s", e)


def note_in_inventory(tickers: list[str], message_id: str | None) -> int:
    try:
        import ticker_inventory
        return ticker_inventory.note_tickers(
            tickers, source="investing_ideas",
            position="Long",  # II is always long ideas
            message_id=message_id,
        ).get("noted", 0)
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def fan_out_refresh(tickers: list[str]) -> dict:
    try:
        import unified_refresh
        return unified_refresh.refresh_all_for_tickers(
            tickers, capture_type="investing_ideas",
            spotgamma_reason="investing_ideas",
        )
    except Exception as e:
        return {"error": str(e)}


def process_email(message_id: str, *, dry_run: bool = False, fan_out: bool = True) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT subject, received_at, html_body FROM hedgeye_emails_raw "
                "WHERE message_id=%s", (message_id,),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "email not found"}
    subject, received_at, html_body = row
    if not is_investing_ideas_subject(subject):
        return {"error": "subject not Investing Ideas", "subject": subject}

    rows = parse_body(html_body)
    snapshot_date = received_at.date() if received_at else date.today()

    summary = {
        "message_id": message_id, "subject": subject,
        "snapshot_date": str(snapshot_date),
        "rows_parsed": len(rows),
        "tickers": [r["ticker"] for r in rows],
        "dry_run": dry_run,
    }
    if dry_run:
        summary["sample"] = rows[:5]
        return summary

    summary["upsert"] = upsert_rows(rows, snapshot_date, message_id)
    tickers = sorted({r["ticker"] for r in rows})
    summary["noted_in_inventory"] = note_in_inventory(tickers, message_id)
    stamp_email_parsed(message_id)
    if tickers and fan_out:
        summary["fan_out"] = fan_out_refresh(tickers)
    elif tickers:
        summary["fan_out"] = "skipped"
    return summary


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "ORDER BY received_at DESC LIMIT 1",
                ("%Top Stock Picks%Leaderboard%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no Investing Ideas emails"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "  AND COALESCE(classified_as,'') <> 'investing_ideas' "
                "ORDER BY received_at ASC",
                ("%Top Stock Picks%Leaderboard%",),
            )
            candidates = cur.fetchall()
    for (mid,) in candidates:
        try:
            process_email(mid, fan_out=fan_out)
            summary["processed"] += 1
        except Exception as e:
            summary["errors"].append({"mid": mid, "err": str(e)})
    return summary


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Investing Ideas parser")
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
