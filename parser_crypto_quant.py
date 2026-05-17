"""CRYPTO QUANT parser (Christian Drake / FIG) — subject-driven.

Bodies are image-only; the signal is in the subject:
  "CRYPTO QUANT | ₿TC (-0.9%), ETH/SOL/AVAX/XRP=BEARISH, XRP=BULLISH,
   SOL (+3.5%)"
Yields per-asset {pct_change, sentiment}. "₿TC" (and cp1252-mangled
"?TC") normalises to BTC.

CLI: py parser_crypto_quant.py [--message-id ID | --latest | --backfill | --probe ID]
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

SUBJECT_RE = re.compile(r"^\s*CRYPTO\s+QUANT\b", re.I)
_NORMALIZE = {"TC": "BTC", "BTC": "BTC", "BITCOIN": "BTC"}
_STOP = {"ETF", "ATH", "AI", "US", "USD", "EU", "AND", "THE", "TO", "VS",
         "DAY", "MMF", "W", "D", "WTD", "DOD", "YTD", "JPM"}  # JPM=bank note


def is_crypto_quant_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def parse_crypto_quant(subject: str) -> list[dict]:
    out = []
    for s in pss.extract_signals(subject, normalize=_NORMALIZE, stop=_STOP):
        out.append({"asset": s["token"], "pct_change": s["pct_change"],
                    "sentiment": s["sentiment"], "raw_token": s["raw"]})
    return out


# ─────────────────────────── Persistence ───────────────────────────

def upsert_rows(rows: list[dict], signal_date: date, message_id: str | None) -> dict:
    import db_pg
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_crypto_quant
                            (signal_date, asset, pct_change, sentiment,
                             raw_token, source_email_id, parsed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (signal_date, asset, source_email_id)
                        DO UPDATE SET
                            pct_change=COALESCE(EXCLUDED.pct_change,
                                          hedgeye_crypto_quant.pct_change),
                            sentiment=COALESCE(EXCLUDED.sentiment,
                                          hedgeye_crypto_quant.sentiment),
                            raw_token=EXCLUDED.raw_token, parsed_at=NOW()
                        """,
                        (signal_date, r["asset"], r["pct_change"],
                         r["sentiment"], r["raw_token"], message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("CRYPTO QUANT upsert failed for %s: %s",
                                r.get("asset"), e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str, classified: str = "crypto_quant") -> None:
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
            pos = ({"bullish": "long", "bearish": "short"}
                   .get(r["sentiment"] or ""))
            n += ticker_inventory.note_tickers(
                [r["asset"]], source="crypto_quant", position=pos,
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
    if not is_crypto_quant_subject(subject):
        return {"error": "subject not CRYPTO QUANT", "subject": subject}

    rows = parse_crypto_quant(subject)
    snapshot_date = received_at.date() if received_at else date.today()
    summary = {
        "message_id": message_id, "subject": subject,
        "signal_date": str(snapshot_date), "rows_parsed": len(rows),
        "rows": rows, "dry_run": dry_run,
    }
    if not rows:
        if not dry_run:
            stamp_email_parsed(message_id, "crypto_quant")
        summary["no_signal"] = True
        return summary
    if dry_run:
        return summary

    summary["upsert"] = upsert_rows(rows, snapshot_date, message_id)
    summary["noted_in_inventory"] = note_in_inventory(rows, message_id)
    stamp_email_parsed(message_id, "crypto_quant")
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
                ("CRYPTO QUANT%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no CRYPTO QUANT emails"}
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
                "('crypto_quant','crypto_quant_parse_failed') "
                "ORDER BY received_at ASC",
                ("CRYPTO QUANT%",),
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
                "('crypto_quant','crypto_quant_parse_failed') "
                "ORDER BY received_at ASC LIMIT %s",
                ("CRYPTO QUANT%", batch_size),
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
    ap = argparse.ArgumentParser(description="CRYPTO QUANT parser")
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
