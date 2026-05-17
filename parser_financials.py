"""Financials Earnings Recap parser — Steiner/FIG team per-ticker recaps.

Subjects: "1Q26 Financial(s) Earnings Recap | XYZ, AFRM, FOUR"
Body per ticker:
  "XYZ ( Best Idea Long ): Leaning Into Lending At The Right Time (Quad 2)
   Summary: ... Recap: ..."

One email yields N rows (one per ticker covered). Body blocks carry the
rating + thesis + quad; the subject ticker list is the fallback.

CLI: py parser_financials.py [--message-id ID | --latest | --backfill | --probe ID]
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

SUBJECT_RE = re.compile(r"\bFinancials?\b.*\bEarnings\s+Recap\b", re.I)

_RATINGS = r"Best\s+Idea\s+Long|Best\s+Idea\s+Short|Long\s+Bench|Short\s+Bench"
# "TICKER ( <Rating> ): <thesis up to Summary/Recap/next block>"
BLOCK_RE = re.compile(
    rf"\b(?P<ticker>[A-Z]{{1,6}}(?:\.[A-Z]{{1,3}})?)\s*\(\s*"
    rf"(?P<rating>{_RATINGS})\s*\)\s*:\s*"
    rf"(?P<thesis>.*?)(?=\s*(?:Summary\s*:|Recap\s*:|"
    rf"\b[A-Z]{{1,6}}\s*\(\s*(?:{_RATINGS})\s*\)|$))",
    re.I | re.S,
)
QUAD_RE = re.compile(r"\(\s*(Quad\s*[1-4])\s*\)", re.I)
SUBJ_TICKERS_RE = re.compile(r"\|\s*(?P<list>[A-Z0-9 ,.&()/FYQ-]+)$")
FEED_ID_RE = re.compile(r"feed_items/(\d+)")
_TICKER = re.compile(r"\b([A-Z]{1,6}(?:\.[A-Z]{1,3})?)\b")
_STOP = {"FY", "Q", "H", "AND", "THE", "KM"}


def is_financials_recap_subject(subject: str) -> bool:
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


def _side(rating: str) -> Optional[str]:
    r = (rating or "").lower()
    if "long" in r:
        return "long"
    if "short" in r:
        return "short"
    return None


def _norm_rating(rating: str) -> str:
    return re.sub(r"\s+", " ", (rating or "").strip()).title()


def parse_financials(subject: str, body_text_or_html: str) -> list[dict]:
    body = _strip_html(body_text_or_html) if "<" in (body_text_or_html or "") \
        else (body_text_or_html or "")
    rows: list[dict] = []
    seen = set()
    for m in BLOCK_RE.finditer(body):
        tk = m.group("ticker").upper()
        if tk in seen or tk in _STOP:
            continue
        seen.add(tk)
        thesis = re.sub(r"\s+", " ", m.group("thesis") or "").strip()
        qm = QUAD_RE.search(thesis)
        quad = re.sub(r"\s+", " ", qm.group(1)).title() if qm else None
        if qm:  # strip the "(Quad N)" tag out of the stored title
            thesis = (thesis[:qm.start()] + thesis[qm.end():]).strip(" -–")
        rows.append({
            "ticker": tk,
            "rating": _norm_rating(m.group("rating")),
            "side": _side(m.group("rating")),
            "thesis_title": (thesis[:300] or None),
            "quad": quad,
        })
    if not rows:
        # Fallback: subject ticker list, no rating/thesis available
        sm = SUBJ_TICKERS_RE.search(re.sub(r"\s+", " ", subject or ""))
        if sm:
            for t in _TICKER.finditer(sm.group("list")):
                tk = t.group(1).upper()
                if tk in _STOP or tk in seen:
                    continue
                seen.add(tk)
                rows.append({"ticker": tk, "rating": None, "side": None,
                             "thesis_title": None, "quad": None})
    return rows


# ─────────────────────────── Persistence ───────────────────────────

def upsert_rows(rows: list[dict], signal_date: date, feed_item_id: int | None,
                message_id: str | None) -> dict:
    import db_pg
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_financials
                            (signal_date, ticker, rating, side, thesis_title,
                             quad, product, feed_item_id, source_email_id,
                             parsed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,'earnings_recap',%s,%s,NOW())
                        ON CONFLICT (signal_date, ticker, source_email_id)
                        DO UPDATE SET rating=EXCLUDED.rating,
                            side=EXCLUDED.side,
                            thesis_title=EXCLUDED.thesis_title,
                            quad=EXCLUDED.quad,
                            feed_item_id=EXCLUDED.feed_item_id, parsed_at=NOW()
                        """,
                        (signal_date, r["ticker"], r["rating"], r["side"],
                         r["thesis_title"], r["quad"], feed_item_id,
                         message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("Financials upsert failed for %s: %s",
                                r.get("ticker"), e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str, classified: str = "financials") -> None:
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
            n += ticker_inventory.note_tickers(
                [r["ticker"]],
                source=ticker_inventory.SOURCE_FINANCIALS_SECTOR_PRO,
                position=r["side"], message_id=message_id,
            ).get("noted", 0)
        return n
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def fan_out_refresh(tickers: list[str]) -> dict:
    try:
        import unified_refresh
        return unified_refresh.refresh_all_for_tickers(
            tickers, capture_type="financials", spotgamma_reason="financials",
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
    if not is_financials_recap_subject(subject):
        return {"error": "subject not Financials Recap", "subject": subject}

    raw = text_body or html_body or ""
    rows = parse_financials(subject, raw)
    snapshot_date = received_at.date() if received_at else date.today()
    body = _strip_html(html_body or "") if not text_body else (text_body or "")
    fm = FEED_ID_RE.search(body)
    feed_item_id = int(fm.group(1)) if fm else None

    summary = {
        "message_id": message_id, "subject": subject,
        "signal_date": str(snapshot_date), "rows_parsed": len(rows),
        "rows": rows, "dry_run": dry_run,
    }
    if not rows:
        if not dry_run:
            stamp_email_parsed(message_id, "financials_parse_failed")
        summary["error"] = "no financials rows extracted"
        return summary
    if dry_run:
        return summary

    summary["upsert"] = upsert_rows(rows, snapshot_date, feed_item_id, message_id)
    summary["noted_in_inventory"] = note_in_inventory(rows, message_id)
    stamp_email_parsed(message_id, "financials")
    tickers = sorted({r["ticker"] for r in rows})
    summary["fan_out"] = fan_out_refresh(tickers) if fan_out else "skipped"
    return summary


def process_one(message_id: str, *, fan_out: bool = True) -> dict:
    return process_email(message_id, fan_out=fan_out)


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ~* %s ORDER BY received_at DESC LIMIT 1",
                (r"Financials?\s.*Earnings\s+Recap",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no Financials Recap emails"}
    return process_email(row[0], dry_run=dry_run)


_SUBJ_SQL = r"subject ~* 'Financials?[[:space:]].*Earnings[[:space:]]+Recap'"


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "failed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "AND COALESCE(classified_as,'') NOT IN "
                "('financials','financials_parse_failed') "
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
                "('financials','financials_parse_failed') "
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
    ap = argparse.ArgumentParser(description="Financials Earnings Recap parser")
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
