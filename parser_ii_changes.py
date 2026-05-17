"""Investing Ideas CHANGES parser — discrete add/remove roster events.

Distinct from parser_investing_ideas (the Top-22 Leaderboard snapshot).
Subjects:
  "4 Changes: Remove CZR, Add ROKU, CARR, SXT"
  "3 Changes: Remove PVH, Add Short DGX, Add AMZN"
  "Top Stock Picks | REMOVE: EFX; ADD: SYF"
  "Top Stock Picks | REMOVE: MPLX & GEL; ADD: DAR & MTDR"
Body (authoritative when present):
  "We are ADDING the following to Investing Ideas : Long: ROKU CARR SXT
   We have REMOVED the following from Investing Ideas : Long: CZR (27.01 - 29.1)
   This position has stopped out on our #VASP ..."

Body is parsed first (carries Long/Short labels + stop-out ranges); the
subject is the fallback when the body is a teaser.

CLI: py parser_ii_changes.py [--message-id ID | --latest | --backfill | --probe ID]
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

# Match "N Changes:" or "Top Stock Picks | REMOVE/ADD" but NOT the Leaderboard
SUBJECT_RE = re.compile(
    r"(^\s*\d+\s+Changes?\s*:)|(\bTop\s+Stock\s+Picks\b.*\b(?:REMOVE|ADD)\b)",
    re.I,
)
LEADERBOARD_RE = re.compile(r"Leaderboard", re.I)

FEED_ID_RE = re.compile(r"feed_items/(\d+)")
_TICKER = r"[A-Z]{1,6}(?:\.[A-Z]{1,3})?"
TICKER_RE = re.compile(rf"\b({_TICKER})\b")
_STOP = {"LONG", "SHORT", "ADD", "REMOVE", "VASP", "AND", "THE", "KM", "EDT",
         "EST", "PM", "AM", "ADDING", "REMOVED", "IDEAS"}

# Each roster move is its own declaration:
#   "We are ADDING the following to Investing Ideas : Long: AMZN"
#   "We have REMOVED the following from Investing Ideas : Long: PVH"
# The ticker payload is the SHORT run right after the side label, terminated
# by a hard boundary (next declaration / narrative / paren range / end). This
# keeps the trailing commentary ("ON AMZN : ...", "shorting-MORE ... (XLV)")
# out of the ticker list.
DECL_RE = re.compile(
    r"\bWe\s+(?:are\s+(?P<add>ADDING)|have\s+(?P<rem>REMOVED))\b[^:]*?"
    r"Investing\s+Ideas\s*:?\s*"
    r"(?P<side>Long|Short)\s*:\s*"
    r"(?P<payload>[A-Z0-9][A-Z0-9 ,&./-]*?)"
    # Tickers are ALL-CAPS, so the first TitleCase word ("These", "We",
    # "Please", "Commentary", ...) or a "(" / end cleanly ends the list.
    r"(?=\s*\(|\s+[A-Z][a-z]|$)"
)  # case-sensitive on purpose: body casing is consistent and the ALL-CAPS
#    ticker vs TitleCase-word distinction is what bounds the payload.
PRICE_RANGE_RE = re.compile(r"\(\s*([0-9.]+)\s*-\s*([0-9.]+)\s*\)")


def is_ii_changes_subject(subject: str) -> bool:
    s = subject or ""
    if LEADERBOARD_RE.search(s):
        return False
    return bool(SUBJECT_RE.search(s))


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


def _tickers(blob: str) -> list[str]:
    out = []
    for m in TICKER_RE.finditer(blob or ""):
        t = m.group(1).upper()
        if t not in _STOP and t not in out:
            out.append(t)
    return out


def _parse_body_decls(body: str) -> list[dict]:
    """Iterate each ADD/REMOVE declaration; tickers come only from the tight
    payload run, so trailing commentary never leaks in."""
    rows: list[dict] = []
    for m in DECL_RE.finditer(body):
        change_type = "add" if m.group("add") else "remove"
        side = m.group("side").lower()
        payload = m.group("payload")
        # an optional "(low - high)" range sits right after the payload
        rng = PRICE_RANGE_RE.search(body[m.end(): m.end() + 40])
        plo = float(rng.group(1)) if rng else None
        phi = float(rng.group(2)) if rng else None
        for tk in _tickers(payload):
            rows.append({"ticker": tk, "change_type": change_type,
                         "side": side, "price_low": plo, "price_high": phi,
                         "reason": None})
    return rows


def _parse_subject(subject: str) -> list[dict]:
    """Fallback: derive add/remove from the subject line."""
    rows: list[dict] = []
    s = re.sub(r"\s+", " ", subject or "").strip()
    s = re.sub(r"^\s*\d+\s+Changes?\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"^.*Top Stock Picks\s*\|\s*", "", s, flags=re.I)
    # Split into "Remove ..." / "Add ..." / "REMOVE: ..." / "ADD: ..." clauses
    parts = re.split(r"[;,|]| and ", s)
    cur_type = None
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"(REMOVE|ADD)\b[: ]*(.*)", p, re.I)
        if m:
            cur_type = "remove" if m.group(1).lower() == "remove" else "add"
            p = m.group(2).strip()
        if cur_type is None:
            continue
        side = "long"
        sm = re.match(r"(Long|Short)\s+(.*)", p, re.I)
        if sm:
            side = sm.group(1).lower()
            p = sm.group(2).strip()
        for tk in _tickers(p):
            rows.append({"ticker": tk, "change_type": cur_type, "side": side,
                         "price_low": None, "price_high": None, "reason": None})
    return rows


def parse_ii_changes(subject: str, body_text_or_html: str) -> list[dict]:
    body = _strip_html(body_text_or_html) if "<" in (body_text_or_html or "") \
        else (body_text_or_html or "")
    rows = _parse_body_decls(body)
    if rows:
        rsn = re.search(r"(stopped out[^.]*\.|This position[^.]*\.)", body, re.I)
        if rsn:
            for r in rows:
                if r["change_type"] == "remove" and r["reason"] is None:
                    r["reason"] = rsn.group(1).strip()[:200]
    else:
        rows = _parse_subject(subject)
    # dedup on (ticker, change_type)
    seen, uniq = set(), []
    for r in rows:
        k = (r["ticker"], r["change_type"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


# ─────────────────────────── Persistence ───────────────────────────

def upsert_rows(rows: list[dict], signal_date: date, message_id: str | None,
                feed_item_id: int | None) -> dict:
    import db_pg
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_ii_changes
                            (signal_date, ticker, change_type, side, price_low,
                             price_high, reason, feed_item_id, source_email_id,
                             parsed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (signal_date, ticker, change_type, source_email_id)
                        DO UPDATE SET side=EXCLUDED.side,
                            price_low=EXCLUDED.price_low,
                            price_high=EXCLUDED.price_high,
                            reason=EXCLUDED.reason,
                            feed_item_id=EXCLUDED.feed_item_id,
                            parsed_at=NOW()
                        """,
                        (signal_date, r["ticker"], r["change_type"], r["side"],
                         r["price_low"], r["price_high"], r["reason"],
                         feed_item_id, message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("II-changes upsert failed for %s: %s",
                                r.get("ticker"), e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str, classified: str = "ii_changes") -> None:
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
            pos = ("closed" if r["change_type"] == "remove"
                   else ("short" if r["side"] == "short" else "long"))
            n += ticker_inventory.note_tickers(
                [r["ticker"]], source=ticker_inventory.SOURCE_INVESTING_IDEA,
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
            tickers, capture_type="ii_changes", spotgamma_reason="ii_changes",
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
    if not is_ii_changes_subject(subject):
        return {"error": "subject not II-changes", "subject": subject}

    rows = parse_ii_changes(subject, text_body or html_body or "")
    snapshot_date = received_at.date() if received_at else date.today()
    fid_m = FEED_ID_RE.search(_strip_html(html_body or "") or (text_body or ""))
    feed_item_id = int(fid_m.group(1)) if fid_m else None

    summary = {
        "message_id": message_id, "subject": subject,
        "signal_date": str(snapshot_date), "rows_parsed": len(rows),
        "rows": rows, "dry_run": dry_run,
    }
    if not rows:
        if not dry_run:
            stamp_email_parsed(message_id, "ii_changes_parse_failed")
        summary["error"] = "no add/remove rows extracted"
        return summary
    if dry_run:
        return summary

    summary["upsert"] = upsert_rows(rows, snapshot_date, message_id, feed_item_id)
    summary["noted_in_inventory"] = note_in_inventory(rows, message_id)
    stamp_email_parsed(message_id, "ii_changes")
    tickers = sorted({r["ticker"] for r in rows})
    summary["fan_out"] = fan_out_refresh(tickers) if fan_out else "skipped"
    return summary


def process_one(message_id: str, *, fan_out: bool = True) -> dict:
    return process_email(message_id, fan_out=fan_out)


_SUBJ_SQL = (r"(subject ~ '^[0-9]+ Changes?:' "
             r"OR subject ILIKE 'Top Stock Picks%REMOVE%' "
             r"OR subject ILIKE 'Top Stock Picks%ADD%') "
             r"AND subject NOT ILIKE '%Leaderboard%'")


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "ORDER BY received_at DESC LIMIT 1")
            row = cur.fetchone()
    if not row:
        return {"error": "no II-changes emails"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "failed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "AND COALESCE(classified_as,'') NOT IN "
                "('ii_changes','ii_changes_parse_failed') "
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
                "('ii_changes','ii_changes_parse_failed') "
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
    ap = argparse.ArgumentParser(description="Investing Ideas Changes parser")
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
