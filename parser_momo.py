"""MOMO Tracker parser (Christian Drake) — subject-driven.

Bodies are image-only; the signal is in the subject:
  "MOMO Tracker | Mag7 (+0.5%), MSFT/ORCL=BULLISH, TSLA (+4%, BULLISH),
   ORCL To Bullish Trend"
Yields per-ticker {pct_change, sentiment}. "Mag7" -> pseudo-ticker MAG7.

CLI: py parser_momo.py [--message-id ID | --latest | --backfill | --probe ID]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date

import parser_subject_signals as pss

log = logging.getLogger(__name__)

SUBJECT_RE = re.compile(r"^\s*MOMO\s+Tracker\b", re.I)
_NORMALIZE = {"MAG": "MAG7", "MAG7": "MAG7"}
_STOP = {"RR", "HATH", "ATH", "ETF", "OPEX", "EPS", "IVOL", "MMF", "AI",
         "US", "USD", "EU", "AND", "THE", "TO", "VS", "DAY", "OPEX",
         "EPS", "GDP", "PPI", "CPI", "PCE", "ISM", "YTD", "DOD", "WTD"}


def is_momo_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def parse_momo(subject: str) -> list[dict]:
    out = []
    for s in pss.extract_signals(subject, normalize=_NORMALIZE, stop=_STOP):
        out.append({"ticker": s["token"], "pct_change": s["pct_change"],
                    "sentiment": s["sentiment"], "raw_token": s["raw"]})
    return out


# ─────────────────────────── Persistence ───────────────────────────

def upsert_rows(rows: list[dict], signal_date: date, message_id: str | None) -> dict:
    import db_pg
    # Write gate: subject-line extraction let prose tokens through (WIDEST)
    # and Hedgeye's own typos (APPL). MAG7 is deliberate and passes via
    # symbol_guard.PSEUDO_INSTRUMENTS.
    from tools.symbol_guard import filter_rows
    rows, _dropped = filter_rows(rows, "momo")
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_momo
                            (signal_date, ticker, pct_change, sentiment,
                             raw_token, source_email_id, parsed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (signal_date, ticker, source_email_id)
                        DO UPDATE SET
                            pct_change=COALESCE(EXCLUDED.pct_change,
                                                hedgeye_momo.pct_change),
                            sentiment=COALESCE(EXCLUDED.sentiment,
                                               hedgeye_momo.sentiment),
                            raw_token=EXCLUDED.raw_token, parsed_at=NOW()
                        """,
                        (signal_date, r["ticker"], r["pct_change"],
                         r["sentiment"], r["raw_token"], message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("MOMO upsert failed for %s: %s",
                                r.get("ticker"), e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str, classified: str = "momo") -> None:
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
            if r["ticker"] == "MAG7":
                continue
            pos = ({"bullish": "long", "bearish": "short"}
                   .get(r["sentiment"] or ""))
            n += ticker_inventory.note_tickers(
                [r["ticker"]], source="momo_tracker", position=pos,
                message_id=message_id,
            ).get("noted", 0)
        return n
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


# ─────────────────────────── Entry points ───────────────────────────

def process_email(message_id: str, *, dry_run: bool = False, fan_out: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT subject, received_at FROM hedgeye_emails_raw "
                "WHERE message_id=%s", (message_id,),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "email not found"}
    subject, received_at = row
    if not is_momo_subject(subject):
        return {"error": "subject not MOMO Tracker", "subject": subject}

    rows = parse_momo(subject)
    snapshot_date = received_at.date() if received_at else date.today()
    summary = {
        "message_id": message_id, "subject": subject,
        "signal_date": str(snapshot_date), "rows_parsed": len(rows),
        "rows": rows, "dry_run": dry_run,
    }
    if not rows:
        # Valid MOMO email whose subject is pure narrative (no ticker/%/
        # sentiment tokens) — classify as momo, not a failure.
        if not dry_run:
            stamp_email_parsed(message_id, "momo")
        summary["no_signal"] = True
        return summary
    if dry_run:
        return summary

    summary["upsert"] = upsert_rows(rows, snapshot_date, message_id)
    summary["noted_in_inventory"] = note_in_inventory(rows, message_id)
    stamp_email_parsed(message_id, "momo")
    return summary


def process_one(message_id: str, *, fan_out: bool = False) -> dict:
    return process_email(message_id, fan_out=fan_out)


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s ORDER BY received_at DESC LIMIT 1",
                ("MOMO Tracker%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no MOMO emails"}
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
                "('momo','momo_parse_failed') ORDER BY received_at ASC",
                ("MOMO Tracker%",),
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
                "('momo','momo_parse_failed') "
                "ORDER BY received_at ASC LIMIT %s",
                ("MOMO Tracker%", batch_size),
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
    ap = argparse.ArgumentParser(description="MOMO Tracker parser")
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
