"""HedgAI Signals parser — Hedgeye's algorithmic multi-asset signal system.

Body carries a fixed 4-section block:
  "Buys Added: Energy XLE , Sugar  Shorts Added: Tesla, Utilities XLU
   Buys Closed: Silver  Shorts Closed:  ( VIEW LARGER IMAGE )"
Each section is a comma-separated list of free-text asset names, frequently
with a trailing ETF ticker ("Energy XLE"); many carry no ticker at all
("Steel", "S&P 500", "Shanghai Composite"), so asset_name is the natural key.

CLI: py parser_hedgai.py [--message-id ID | --latest | --backfill | --probe ID]
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

SUBJECT_RE = re.compile(r"\bHedgAI\s+Signals\b", re.I)

# Capture the four ordered sections; any may be empty.
SECTIONS_RE = re.compile(
    r"Buys?\s+Added\s*:\s*(?P<buy_add>.*?)\s*"
    r"Shorts?\s+Added\s*:\s*(?P<short_add>.*?)\s*"
    r"Buys?\s+Closed\s*:\s*(?P<buy_close>.*?)\s*"
    r"Shorts?\s+Closed\s*:\s*(?P<short_close>.*?)\s*"
    r"(?=\(\s*VIEW\s+LARGER|System\s+Expansion|Please\s+visit|$)",
    re.I | re.S,
)
TRAIL_TICKER_RE = re.compile(r"\b([A-Z]{2,5})\s*$")
BODY_TS_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+E[DS]T", re.I)
FEED_ID_RE = re.compile(r"feed_items/(\d+)")

_ACTIONS = [
    ("buy_add",     "long",  False),
    ("short_add",   "short", False),
    ("buy_close",   "long",  True),
    ("short_close", "short", True),
]


def is_hedgai_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


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


def _split_assets(seg: str) -> list[str]:
    out = []
    for raw in (seg or "").split(","):
        name = raw.strip().strip("-–").strip()
        # collapse internal whitespace; drop noise tokens
        name = re.sub(r"\s+", " ", name)
        if name and name.lower() not in {"none", "n/a", "-"}:
            out.append(name)
    return out


def _ticker_of(asset_name: str) -> Optional[str]:
    m = TRAIL_TICKER_RE.search(asset_name)
    if not m:
        return None
    tk = m.group(1).upper()
    # Filter common all-caps words that aren't tickers
    if tk in {"US", "UST", "EUR", "USD", "ETF", "SPX"}:
        return None
    return tk


def parse_hedgai(body_text_or_html: str) -> tuple[bool, list[dict]]:
    """Return (sections_found, rows). sections_found=True with rows=[] means a
    valid HedgAI email with no signal changes that day (all sections empty) —
    NOT a parse failure."""
    body = _strip_html(body_text_or_html) if "<" in (body_text_or_html or "") \
        else (body_text_or_html or "")
    m = SECTIONS_RE.search(body)
    if not m:
        return False, []
    rows: list[dict] = []
    for action, side, is_close in _ACTIONS:
        for name in _split_assets(m.group(action)):
            rows.append({
                "asset_name": name[:200],
                "ticker":     _ticker_of(name),
                "action":     action,
                "side":       side,
                "is_close":   is_close,
            })
    return True, rows


# ─────────────────────────── Persistence ───────────────────────────

def upsert_rows(rows: list[dict], signal_date: date, signal_ts,
                feed_item_id: int | None, message_id: str | None) -> dict:
    import db_pg
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_hedgai
                            (signal_date, signal_ts, asset_name, ticker, action,
                             side, is_close, feed_item_id, source_email_id,
                             parsed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (signal_date, asset_name, action, source_email_id)
                        DO UPDATE SET signal_ts=EXCLUDED.signal_ts,
                            ticker=EXCLUDED.ticker, side=EXCLUDED.side,
                            is_close=EXCLUDED.is_close,
                            feed_item_id=EXCLUDED.feed_item_id, parsed_at=NOW()
                        """,
                        (signal_date, signal_ts, r["asset_name"], r["ticker"],
                         r["action"], r["side"], r["is_close"], feed_item_id,
                         message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("HedgAI upsert failed for %s: %s",
                                r.get("asset_name"), e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str, classified: str = "hedgai") -> None:
    import db_pg
    try:
        db_pg.mark_email_classified(message_id, classified)
    except Exception as e:
        log.warning("stamp_email_parsed failed: %s", e)


def note_in_inventory(rows: list[dict], message_id: str | None) -> int:
    try:
        import ticker_inventory
        n = 0
        for r in rows:
            if not r["ticker"]:
                continue
            pos = ("closed" if r["is_close"]
                   else ("short" if r["side"] == "short" else "long"))
            n += ticker_inventory.note_tickers(
                [r["ticker"]], source=ticker_inventory.SOURCE_HEDGAI_SIGNALS,
                position=pos, message_id=message_id,
            ).get("noted", 0)
        return n
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def fan_out_refresh(tickers: list[str]) -> dict:
    try:
        import unified_refresh
        return unified_refresh.refresh_all_for_tickers(
            tickers, capture_type="hedgai", spotgamma_reason="hedgai",
        )
    except Exception as e:
        return {"error": str(e)}


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
    if not is_hedgai_subject(subject):
        return {"error": "subject not HedgAI", "subject": subject}

    raw = text_body or html_body or ""
    sections_found, rows = parse_hedgai(raw)
    snapshot_date = received_at.date() if received_at else date.today()
    body = _strip_html(html_body or "") if not text_body else text_body
    tsm = BODY_TS_RE.search(body or "")
    signal_ts = None
    if tsm:
        try:
            signal_ts = datetime.strptime(
                f"{tsm.group(1)} {tsm.group(2).replace(' ', '')}",
                "%m/%d/%Y %I:%M%p")
        except ValueError:
            signal_ts = None
    fm = FEED_ID_RE.search(body or "")
    feed_item_id = int(fm.group(1)) if fm else None

    summary = {
        "message_id": message_id, "subject": subject,
        "signal_date": str(snapshot_date), "rows_parsed": len(rows),
        "rows": rows, "dry_run": dry_run,
    }
    if not sections_found:
        if not dry_run:
            stamp_email_parsed(message_id, "hedgai_parse_failed")
        summary["error"] = "HedgAI section block not found"
        return summary
    if dry_run:
        return summary

    # sections_found with rows=[] is a valid "no changes today" snapshot:
    # classify as hedgai (not a failure) so it isn't reprocessed forever.
    summary["no_changes"] = not rows
    if rows:
        summary["upsert"] = upsert_rows(rows, snapshot_date, signal_ts,
                                        feed_item_id, message_id)
    summary["noted_in_inventory"] = note_in_inventory(rows, message_id)
    stamp_email_parsed(message_id, "hedgai")
    tickers = sorted({r["ticker"] for r in rows if r["ticker"]})
    summary["fan_out"] = fan_out_refresh(tickers) if (fan_out and tickers) \
        else "skipped"
    return summary


def process_one(message_id: str, *, fan_out: bool = True) -> dict:
    return process_email(message_id, fan_out=fan_out)


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s ORDER BY received_at DESC LIMIT 1",
                ("%HedgAI Signals%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no HedgAI emails"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "failed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "  AND COALESCE(classified_as,'') NOT IN "
                "('hedgai','hedgai_parse_failed') ORDER BY received_at ASC",
                ("%HedgAI Signals%",),
            )
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
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "  AND COALESCE(classified_as,'') NOT IN "
                "('hedgai','hedgai_parse_failed') "
                "ORDER BY received_at ASC LIMIT %s",
                ("%HedgAI Signals%", batch_size),
            )
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
    ap = argparse.ArgumentParser(description="HedgAI Signals parser")
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
