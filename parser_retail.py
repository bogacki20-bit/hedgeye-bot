"""Retail product parser — Brian McGough's retail callout feeds.

Three structured text products (image-only "Position Monitors | Retail"
is handled by the multi-sector Position Monitors parser):

  soundbites   "Actionable Retail Soundbites and Daily Callouts M/D"
               body: "Today's Callouts Include: AMZN, BOOT, BIRK, ..."
  key_callouts "KEY RETAIL CALLOUTS FOR THE WEEK | M/D"
               body: "WRBY / REAL / VVV / ..." + LONG/SHORT COVERAGE: sections
  sunday_edge  "Sunday Retail EDGE | Going Active Long PLNT, ATZ, VVV"
               subject tickers + body "Takeaway: WRBY, REAL, ..."

Lists mix tickers with company names and macro topics; only clean ticker
tokens are kept (a stoplist filters non-ticker all-caps acronyms).

CLI: py parser_retail.py [--message-id ID | --latest | --backfill | --probe ID]
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

SOUNDBITES_RE = re.compile(r"Actionable\s+Retail\s+Soundbites", re.I)
KEY_CALLOUTS_RE = re.compile(r"KEY\s+RETAIL\s+CALLOUTS", re.I)
SUNDAY_EDGE_RE = re.compile(r"Sunday\s+Retail\s+EDGE", re.I)

TICKER_TOK = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z]{1,3})?$")
FEED_ID_RE = re.compile(r"feed_items/(\d+)")

# All-caps tokens that appear in these lists but are NOT tickers.
_STOP = {
    "CPI", "MBA", "NICS", "AWS", "EDT", "EST", "PM", "AM", "KM", "US", "USD",
    "EU", "FY", "Q", "AND", "THE", "OR", "FOR", "EARNINGS", "PREVIEWS",
    "LONG", "SHORT", "COVERAGE", "KEY", "RETAIL", "CALLOUTS", "WEEK", "TODAY",
    "INCLUDE", "TAKEAWAY", "EDGE", "ACTIVE", "BEST", "IDEA", "DATA", "GAS",
    "ETF", "GDP", "PPI", "ISM", "PCE", "DOW", "SPX", "VIX", "IPO", "CEO",
    "CFO", "ATH", "YTD", "DOD", "WTD", "MTD", "EPS", "AI", "EV", "TBD",
}


def detect_product(subject: str) -> Optional[str]:
    s = subject or ""
    if SOUNDBITES_RE.search(s):
        return "soundbites"
    if KEY_CALLOUTS_RE.search(s):
        return "key_callouts"
    if SUNDAY_EDGE_RE.search(s):
        return "sunday_edge"
    return None


def is_retail_subject(subject: str) -> bool:
    return detect_product(subject) is not None


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


def _tickers_from(segment: str) -> list[str]:
    """Split on , / | and keep only clean single-token tickers (used for the
    soundbites / key_callouts body lists, where multi-word entries are
    company names or macro topics to be dropped)."""
    out: list[str] = []
    for raw in re.split(r"[,/|]", segment or ""):
        tok = raw.strip().strip(".").strip()
        if " " in tok:                       # multi-word -> company/macro topic
            continue
        tu = tok.upper()
        if TICKER_TOK.match(tu) and tu not in _STOP and tu not in out:
            out.append(tu)
    return out


# min 2 chars: the scan path runs over free text where 1-letter all-caps
# tokens ("L" from a bullet, "K") are noise, not tickers.
_SCAN_RE = re.compile(r"\b[A-Z]{2,5}(?:\.[A-Z]{1,3})?\b")


def _scan_tickers(text: str) -> list[str]:
    """Scan ALL-CAPS ticker tokens out of free text (subject lines /
    'Takeaway:' runs) where tickers are preceded by lowercase words like
    'Going Active Long'."""
    out: list[str] = []
    for m in _SCAN_RE.finditer(text or ""):
        tu = m.group(0).upper()
        if TICKER_TOK.match(tu) and tu not in _STOP and tu not in out:
            out.append(tu)
    return out


def parse_retail(subject: str, body_text_or_html: str) -> list[dict]:
    product = detect_product(subject)
    if not product:
        return []
    body = _strip_html(body_text_or_html) if "<" in (body_text_or_html or "") \
        else (body_text_or_html or "")
    subject = re.sub(r"\s+", " ", subject or "").strip()
    rows: list[dict] = []
    seen: set[str] = set()

    def add(tk: str, side: Optional[str]):
        if tk in seen:
            return
        seen.add(tk)
        rows.append({"ticker": tk, "product": product, "side": side})

    if product == "soundbites":
        m = re.search(r"Callouts\s+Include\s*:\s*(?P<list>.+?)"
                      r"(?=\bWe[''’]?re\b|\bWe\s+are\b|\.\s|$)", body, re.I)
        seg = m.group("list") if m else ""
        for tk in _tickers_from(seg):
            add(tk, None)

    elif product == "key_callouts":
        # Header slash-list (between "CALLOUTS | <date>" and a COVERAGE section)
        hm = re.search(r"KEY\s+RETAIL\s+CALLOUTS\s*\|\s*[0-9/]+\s*(?P<list>.+?)"
                       r"(?=\bLONG\s+COVERAGE\b|\bSHORT\s+COVERAGE\b|$)",
                       body, re.I)
        if hm:
            for tk in _tickers_from(hm.group("list")):
                add(tk, None)
        for side, pat in (("long", r"LONG\s+COVERAGE\s*:\s*(?P<seg>.+?)"
                                    r"(?=\bSHORT\s+COVERAGE\b|\bLONG\s+COVERAGE\b|$)"),
                          ("short", r"SHORT\s+COVERAGE\s*:\s*(?P<seg>.+?)"
                                    r"(?=\bLONG\s+COVERAGE\b|\bSHORT\s+COVERAGE\b|$)")):
            for sm in re.finditer(pat, body, re.I | re.S):
                # first token after the section header is the covered ticker
                for tk in _tickers_from(sm.group("seg")[:40]):
                    if tk in seen:                # upgrade side if already added
                        for r in rows:
                            if r["ticker"] == tk and r["side"] is None:
                                r["side"] = side
                    else:
                        add(tk, side)
                    break

    elif product == "sunday_edge":
        subj_side = None
        sl = subject.lower()
        if "short" in sl:
            subj_side = "short"
        elif "long" in sl:
            subj_side = "long"
        # subject after the "|" (e.g. "Going Active Long PLNT, ATZ, VVV")
        subj_seg = subject.split("|", 1)[1] if "|" in subject else ""
        for tk in _scan_tickers(subj_seg):
            add(tk, subj_side)
        # Body "Takeaway:" run — often "Long WRBY, BBWI, AS. Short BOOT,
        # GIII. Previews on AMZN, W". Split into Long/Short/other spans so
        # each ticker gets the right side.
        tm = re.search(r"Takeaway\s*:\s*(?P<seg>.{0,400}?)"
                       r"(?=\bTOP\s+\d|\bEARNINGS\b|\bPREVIEWS?\b\s*:|$)",
                       body, re.I | re.S)
        if tm:
            seg = tm.group("seg")
            spans = [
                ("long",  re.search(r"\bLong\b(?P<t>.*?)(?=\bShort\b|"
                                    r"\bPreview|$)", seg, re.I | re.S)),
                ("short", re.search(r"\bShort\b(?P<t>.*?)(?=\bLong\b|"
                                    r"\bPreview|$)", seg, re.I | re.S)),
            ]
            covered = False
            for sd, mm in spans:
                if mm:
                    covered = True
                    for tk in _scan_tickers(mm.group("t")):
                        add(tk, sd)
            # tickers in the segment not under an explicit Long/Short label
            for tk in _scan_tickers(seg):
                add(tk, subj_side if not covered else None)

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
                        INSERT INTO hedgeye_retail
                            (signal_date, ticker, product, side, feed_item_id,
                             source_email_id, parsed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT (signal_date, ticker, product, source_email_id)
                        DO UPDATE SET side=COALESCE(EXCLUDED.side,
                                                    hedgeye_retail.side),
                            feed_item_id=EXCLUDED.feed_item_id, parsed_at=NOW()
                        """,
                        (signal_date, r["ticker"], r["product"], r["side"],
                         feed_item_id, message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("Retail upsert failed for %s: %s",
                                r.get("ticker"), e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str, classified: str = "retail") -> None:
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
                [r["ticker"]], source=ticker_inventory.SOURCE_RETAIL_PRO,
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
            tickers, capture_type="retail", spotgamma_reason="retail",
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
    if not is_retail_subject(subject):
        return {"error": "subject not Retail", "subject": subject}

    raw = text_body or html_body or ""
    rows = parse_retail(subject, raw)
    snapshot_date = received_at.date() if received_at else date.today()
    body = _strip_html(html_body or "") if not text_body else (text_body or "")
    fm = FEED_ID_RE.search(body)
    feed_item_id = int(fm.group(1)) if fm else None

    summary = {
        "message_id": message_id, "subject": subject,
        "product": detect_product(subject),
        "signal_date": str(snapshot_date), "rows_parsed": len(rows),
        "rows": rows, "dry_run": dry_run,
    }
    if not rows:
        if not dry_run:
            stamp_email_parsed(message_id, "retail_parse_failed")
        summary["error"] = "no retail tickers extracted"
        return summary
    if dry_run:
        return summary

    summary["upsert"] = upsert_rows(rows, snapshot_date, feed_item_id, message_id)
    summary["noted_in_inventory"] = note_in_inventory(rows, message_id)
    stamp_email_parsed(message_id, "retail")
    tickers = sorted({r["ticker"] for r in rows})
    summary["fan_out"] = fan_out_refresh(tickers) if fan_out else "skipped"
    return summary


def process_one(message_id: str, *, fan_out: bool = True) -> dict:
    return process_email(message_id, fan_out=fan_out)


_SUBJ_SQL = ("(subject ILIKE 'Actionable Retail Soundbites%' "
             "OR subject ILIKE 'KEY RETAIL CALLOUTS%' "
             "OR subject ILIKE 'Sunday Retail EDGE%')")


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "ORDER BY received_at DESC LIMIT 1")
            row = cur.fetchone()
    if not row:
        return {"error": "no Retail emails"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "failed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
                "AND COALESCE(classified_as,'') NOT IN "
                "('retail','retail_parse_failed') ORDER BY received_at ASC")
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
                "('retail','retail_parse_failed') "
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
    ap = argparse.ArgumentParser(description="Retail product parser")
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
