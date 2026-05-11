"""Signal Strength parser — Hedgeye's curated ~90-stock watchlist.

Emails look like:
  Subject: "Signal Strength Stocks: 90 Stocks (2 Added, 3 Removed)"
  Body:    "Added: WRBY, AS / Removed: CVX, BROS, JBHT"
           (Full list is image-only in the email; deltas are text.)

This parser captures the adds/removes as deltas in hedgeye_signal_strength
with is_added_today / is_removed_today flags. The full list is reconstructable
by carrying forward prior membership and applying daily deltas.

CLI:
  py parser_signal_strength.py --message-id <id>
  py parser_signal_strength.py --latest
  py parser_signal_strength.py --backfill
  py parser_signal_strength.py --probe <id>
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


SUBJECT_RE = re.compile(r"Signal\s+Strength\s+Stocks", re.I)


def is_signal_strength_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["style", "script"]):
            t.decompose()
        return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    except ImportError:
        text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()


ADDED_RE   = re.compile(r"\bAdded\s*:\s*([A-Z0-9,\s]+?)(?=\bRemoved|\bRANK|\bPlease|\Z)",
                       re.I | re.S)
REMOVED_RE = re.compile(r"\bRemoved\s*:\s*([A-Z0-9,\s]+?)(?=\bAdded|\bRANK|\bPlease|\Z)",
                       re.I | re.S)
TICKER_RE  = re.compile(r"\b([A-Z]{1,5})\b")


def parse_body(html_body: str) -> dict:
    text = _strip_html(html_body)
    out = {"added": [], "removed": []}

    am = ADDED_RE.search(text)
    if am:
        for tm in TICKER_RE.finditer(am.group(1)):
            tk = tm.group(1)
            if tk not in {"RANK", "PLEASE", "AND", "THE"}:
                out["added"].append(tk)

    rm = REMOVED_RE.search(text)
    if rm:
        for tm in TICKER_RE.finditer(rm.group(1)):
            tk = tm.group(1)
            if tk not in {"RANK", "PLEASE", "AND", "THE"}:
                out["removed"].append(tk)

    # Dedup preserving order
    out["added"]   = list(dict.fromkeys(out["added"]))
    out["removed"] = list(dict.fromkeys(out["removed"]))
    return out


# ─────────────────────────── Persistence ───────────────────────────

def upsert_deltas(deltas: dict, snapshot_date: date, message_id: str | None) -> dict:
    import db_pg
    written, failed = 0, 0
    rows = (
        [(t, True,  False) for t in deltas.get("added", [])]
        + [(t, False, True) for t in deltas.get("removed", [])]
    )
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for ticker, is_added, is_removed in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_signal_strength
                            (snapshot_date, ticker, is_added_today,
                             is_removed_today, source_email_id, parsed_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (snapshot_date, ticker) DO UPDATE
                            SET is_added_today   = EXCLUDED.is_added_today,
                                is_removed_today = EXCLUDED.is_removed_today,
                                source_email_id  = EXCLUDED.source_email_id,
                                parsed_at        = NOW()
                        """,
                        (snapshot_date, ticker, is_added, is_removed, message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("SS upsert failed for %s: %s", ticker, e)
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
                    "SET classified_as='signal_strength', parsed_at=NOW() "
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
            tickers, source="signal_strength",
            message_id=message_id,
        ).get("noted", 0)
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def fan_out_refresh(tickers: list[str]) -> dict:
    try:
        import unified_refresh
        return unified_refresh.refresh_all_for_tickers(
            tickers, capture_type="signal_strength",
            spotgamma_reason="signal_strength",
        )
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────── Entry points ───────────────────────────

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
    if not is_signal_strength_subject(subject):
        return {"error": "subject not Signal Strength", "subject": subject}

    parsed = parse_body(html_body)
    snapshot_date = received_at.date() if received_at else date.today()

    summary = {
        "message_id":    message_id, "subject": subject,
        "snapshot_date": str(snapshot_date),
        "added":         parsed["added"],
        "removed":       parsed["removed"],
        "dry_run":       dry_run,
    }
    if dry_run:
        return summary

    summary["upsert"] = upsert_deltas(parsed, snapshot_date, message_id)
    all_tickers = sorted(set(parsed["added"]) | set(parsed["removed"]))
    summary["noted_in_inventory"] = note_in_inventory(all_tickers, message_id)
    stamp_email_parsed(message_id)
    if all_tickers and fan_out:
        summary["fan_out"] = fan_out_refresh(all_tickers)
    elif all_tickers:
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
                ("%Signal Strength Stocks%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no Signal Strength emails"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "  AND COALESCE(classified_as,'') <> 'signal_strength' "
                "ORDER BY received_at ASC",
                ("%Signal Strength Stocks%",),
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
    ap = argparse.ArgumentParser(description="Signal Strength parser")
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
