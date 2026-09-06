"""Real-Time Alert (RTA) parser — Keith/analyst single-name trade signals.

One email == one signal. Subject grammar:
  **Real-Time Alert: [<Analyst> ]<SignalType> Signal (<note>): <Company>[ (<TICKER>)] [-KM]
e.g.
  **Real-Time Alert: Van Sciver Buy Signal (buying stocks): Caterpillar (CAT) -KM
  **Real-Time Alert: Cover-SOME Signal (no stress): THC -KM
  **Real-Time Alert: Macro Sell Signal (stop losses): FXB -KM

Body carries the authoritative ticker/price on a signal line:
  "05/15/2026 12:03 PM EDT  BUY SIGNAL CAT $883.46"
  "SELL SIGNAL - COVERING SHORT THC $197.90"
plus an app.hedgeye.com/feed_items/<id> permalink.

Ticker resolution priority: body signal line > subject "(TICKER)" > subject tail token.

CLI:
  py parser_rta.py --message-id <id>
  py parser_rta.py --latest
  py parser_rta.py --backfill
  py parser_rta.py --probe <id>
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

SUBJECT_RE = re.compile(r"Real-?Time\s+Alert", re.I)

# Robust split: "(note): tail" is reliable even when "Signal" is mistyped
# ("Sigal", "Singal", "Signals", "Cover-SOME Side", etc — all real Hedgeye typos).
ALERT_HEAD_RE = re.compile(r"Real-?Time\s+Alert\s*:?\s*", re.I)
NOTE_TAIL_RE = re.compile(
    r"\((?P<note>[^)]*)\)\s*:\s*(?P<tail>.+?)\s*(?:-\s*KM\s*)?$",
    re.I,
)
# Signal-type keyword anywhere near the end of <head>; tolerate a trailing
# garbled "Signal"-ish / "Side" / "Signals" token after it.
SIGTYPE_RE = re.compile(
    r"\b(?P<sig>(?:Buy|Sell|Cover|Add|Trim|Short)(?:[- ]?SOME|[- ]?MORE)?)\b"
    r"(?:\s+(?:S\w*l|S\w*g\w*|Side|Signals?))?\s*$",
    re.I,
)
SUBJ_TICKER_PAREN_RE = re.compile(r"\(([A-Z]{1,6}(?:\.[A-Z]{1,3})?)\)")
KM_FLAG_RE = re.compile(r"-\s*KM\b", re.I)

# Body signal line, with all observed qualifier variants:
#   "BUY SIGNAL CAT $883.46"
#   "BUY SIGNAL - COVERING SHORT THC $197.90"
#   "SELL SIGNAL - SHORTING DGX $191.78"
# The qualifier is any "- WORD(S)" between SIGNAL and the ticker; the ticker is
# the all-caps token immediately preceding the $price.
# Case-sensitive: body signal lines are all-uppercase, so the ticker is an
# uppercase token; this prevents [A-Z] matching lowercase company words.
BODY_SIGNAL_RE = re.compile(
    r"\b(?P<action>BUY|SELL)\s+SIGNAL\b[ \-]*(?P<qual>[A-Z][A-Z ]*?)?\s*"
    r"\b(?P<ticker>[A-Z]{1,6}(?:\.[A-Z]{1,3})?)\s+\$\s*(?P<price>[0-9][0-9,]*\.?[0-9]*)"
)
BODY_TS_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+(E[DS]T)",
    re.I,
)
FEED_ID_RE = re.compile(r"feed_items/(\d+)")

_TICKER_STOP = {"BUY", "SELL", "SIGNAL", "KM", "THE", "AND", "EDT", "EST",
                "SHORT", "LONG", "COVER", "SOME", "MORE", "PM", "AM", "HEDGEYE"}


def is_rta_subject(subject: str) -> bool:
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
        text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_sigtype(raw: str) -> str:
    s = re.sub(r"\s+", "", (raw or "")).lower()  # "Sell-SOME" -> "sell-some"
    # canonicalize "sellsome"/"sell some" -> "sell-some" (etc.)
    return re.sub(r"^(buy|sell|cover|add|trim|short)-?(some|more)$",
                  r"\1-\2", s)


def _side_for(signal_type: str, is_cover_short: bool) -> Optional[str]:
    st = signal_type or ""
    if is_cover_short or st.startswith("cover"):
        return "short"          # covering reduces a short position
    if st.startswith("buy") or st.startswith("add"):
        return "long"
    if st.startswith("sell") or st.startswith("trim") or st.startswith("short"):
        return "short"
    return None


def parse_rta(subject: str, body_text_or_html: str) -> Optional[dict]:
    """Return one RTA dict, or None if the subject doesn't structurally match."""
    subject = re.sub(r"\s+", " ", subject or "").strip()
    body = _strip_html(body_text_or_html) if "<" in (body_text_or_html or "") \
        else (body_text_or_html or "")

    hm = ALERT_HEAD_RE.search(subject)
    if not hm:
        return None
    after = subject[hm.end():]
    km_flag = bool(KM_FLAG_RE.search(subject))

    nm = NOTE_TAIL_RE.search(after)
    if nm:
        head = after[: nm.start()].strip()
        note = (nm.group("note") or "").strip() or None
        tail = (nm.group("tail") or "").strip()
    else:
        # No "(note): tail" — e.g. "correction on EWP". Whole remainder is head;
        # ticker (if any) recovered from body / trailing token below.
        head, note, tail = after.strip(), None, ""

    analyst, sig_raw = None, None
    stm = SIGTYPE_RE.search(head)
    if stm:
        sig_raw = stm.group("sig")
        analyst = head[: stm.start()].strip() or None
    signal_type = _norm_sigtype(sig_raw) if sig_raw else None

    # Subject tail: "Caterpillar (CAT) -KM"  -> company + paren ticker
    tail_clean = KM_FLAG_RE.sub("", tail).strip().rstrip(":").strip()
    subj_ticker = None
    pm = SUBJ_TICKER_PAREN_RE.search(tail_clean)
    company_name = None
    if pm:
        subj_ticker = pm.group(1).upper()
        company_name = tail_clean[: pm.start()].strip(" ()-") or None
    else:
        # No parenthetical: tail itself may be the bare ticker (e.g. "THC")
        toks = [t for t in re.split(r"\s+", tail_clean) if t]
        if len(toks) == 1 and re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z]{1,3})?", toks[0]):
            subj_ticker = toks[0].upper()
        else:
            company_name = tail_clean or None

    # Body signal line — authoritative for action/ticker/price
    action = price = body_ticker = None
    is_cover_short = False
    bm = BODY_SIGNAL_RE.search(body)
    if bm:
        action = bm.group("action").upper()
        is_cover_short = "COVER" in (bm.group("qual") or "").upper()
        body_ticker = bm.group("ticker").upper()
        try:
            price = float(bm.group("price").replace(",", ""))
        except ValueError:
            price = None

    ticker = body_ticker or subj_ticker
    if not ticker or ticker in _TICKER_STOP:
        # last-ditch: a lone all-caps token in the tail
        for t in re.split(r"\s+", tail_clean):
            tu = t.strip("()").upper()
            if re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z]{1,3})?", tu) and tu not in _TICKER_STOP:
                ticker = tu
                break
    if not ticker:
        return None

    signal_ts = None
    tsm = BODY_TS_RE.search(body)
    if tsm:
        try:
            signal_ts = datetime.strptime(
                f"{tsm.group(1)} {tsm.group(2).replace(' ', '')}",
                "%m/%d/%Y %I:%M%p",
            )
        except ValueError:
            signal_ts = None

    feed_item_id = None
    fm = FEED_ID_RE.search(body)
    if fm:
        feed_item_id = int(fm.group(1))

    return {
        "ticker":         ticker.upper(),
        "company_name":   company_name,
        "analyst":        analyst,
        "signal_type":    signal_type,
        "action":         action,
        "is_cover_short": is_cover_short,
        "side":           _side_for(signal_type or "", is_cover_short),
        "price":          price,
        "km_flag":        km_flag,
        "note":           note,
        "feed_item_id":   feed_item_id,
        "signal_ts":      signal_ts,
    }


# ─────────────────────────── Persistence ───────────────────────────

def upsert_row(rec: dict, signal_date: date, message_id: str | None) -> dict:
    import db_pg
    # Write gate: BODY_SIGNAL_RE takes "the all-caps token immediately
    # preceding the $price", which captured MORRIS out of a spelled-out
    # "PHILIP MORRIS $190.60" line — a company-name word stored as a ticker
    # WITH Philip Morris's price. Known names pass on membership; word
    # tokens resolve nowhere and are dropped loudly.
    try:
        from tools.symbol_guard import validate_for_storage
        kept, dropped = validate_for_storage([rec.get("ticker")], "rta")
        if dropped:
            return {"written": 0, "failed": 0,
                    "dropped": [str(d) for d in dropped]}
    except Exception as e:
        log.warning("symbol_guard unavailable, RTA row unvalidated: %s", e)
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO hedgeye_rta
                        (signal_date, signal_ts, ticker, company_name, analyst,
                         signal_type, action, is_cover_short, side, price,
                         km_flag, note, feed_item_id, source_email_id, parsed_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                    ON CONFLICT (signal_date, ticker, source_email_id) DO UPDATE
                        SET signal_ts=EXCLUDED.signal_ts,
                            company_name=EXCLUDED.company_name,
                            analyst=EXCLUDED.analyst,
                            signal_type=EXCLUDED.signal_type,
                            action=EXCLUDED.action,
                            is_cover_short=EXCLUDED.is_cover_short,
                            side=EXCLUDED.side,
                            price=EXCLUDED.price,
                            km_flag=EXCLUDED.km_flag,
                            note=EXCLUDED.note,
                            feed_item_id=EXCLUDED.feed_item_id,
                            parsed_at=NOW()
                    """,
                    (signal_date, rec["signal_ts"], rec["ticker"],
                     rec["company_name"], rec["analyst"], rec["signal_type"],
                     rec["action"], rec["is_cover_short"], rec["side"],
                     rec["price"], rec["km_flag"], rec["note"],
                     rec["feed_item_id"], message_id),
                )
                conn.commit()
                return {"written": 1, "failed": 0}
            except Exception as e:
                conn.rollback()
                log.warning("RTA upsert failed for %s: %s", rec.get("ticker"), e)
                return {"written": 0, "failed": 1}


def stamp_email_parsed(message_id: str, classified: str = "rta") -> None:
    import db_pg
    try:
        db_pg.mark_email_classified(message_id, classified)
    except Exception as e:
        log.warning("stamp_email_parsed failed: %s", e)


def note_in_inventory(ticker: str, position: str | None, message_id: str | None) -> int:
    try:
        import ticker_inventory
        return ticker_inventory.note_tickers(
            [ticker], source=ticker_inventory.SOURCE_RTA,
            position=position, message_id=message_id,
        ).get("noted", 0)
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def fan_out_refresh(tickers: list[str]) -> dict:
    try:
        import unified_refresh
        return unified_refresh.refresh_all_for_tickers(
            tickers, capture_type="rta", spotgamma_reason="rta",
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
    if not is_rta_subject(subject):
        return {"error": "subject not RTA", "subject": subject}

    rec = parse_rta(subject, text_body or html_body or "")
    snapshot_date = received_at.date() if received_at else date.today()

    if not rec:
        if not dry_run:
            stamp_email_parsed(message_id, "rta_parse_failed")
        return {"error": "subject matched but no RTA extracted",
                "subject": subject, "message_id": message_id}

    summary = {
        "message_id": message_id, "subject": subject,
        "signal_date": str(snapshot_date),
        "ticker": rec["ticker"], "analyst": rec["analyst"],
        "signal_type": rec["signal_type"], "action": rec["action"],
        "price": rec["price"], "dry_run": dry_run, "parsed": rec,
    }
    if dry_run:
        return summary

    summary["upsert"] = upsert_row(rec, snapshot_date, message_id)
    summary["noted_in_inventory"] = note_in_inventory(
        rec["ticker"], rec["side"], message_id)
    stamp_email_parsed(message_id, "rta")
    # Cross-signal (operator spec 2026-07-11): match the RTA against the book
    # + SS roster, alert with facts, and same-day-suppress on full closes.
    # Guarded — a cross-signal failure never blocks the RTA pipeline.
    try:
        from tools.rta_cross_signal import handle_rta
        summary["cross_signal"] = handle_rta(rec, snapshot_date, message_id)
    except Exception as e:
        log.warning("rta cross-signal failed for %s: %s", rec.get("ticker"), e)
        summary["cross_signal"] = {"error": str(e)}
    if fan_out:
        summary["fan_out"] = fan_out_refresh([rec["ticker"]])
    else:
        summary["fan_out"] = "skipped"
    return summary


# email_parser dispatches inline via process_one(message_id, fan_out=...)
def process_one(message_id: str, *, fan_out: bool = True) -> dict:
    return process_email(message_id, fan_out=fan_out)


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s ORDER BY received_at DESC LIMIT 1",
                ("%Real-Time Alert%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no RTA emails"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "failed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "  AND COALESCE(classified_as,'') NOT IN ('rta','rta_parse_failed') "
                "ORDER BY received_at ASC",
                ("%Real-Time Alert%",),
            )
            candidates = cur.fetchall()
    for (mid,) in candidates:
        try:
            r = process_email(mid, fan_out=fan_out)
            if r.get("error"):
                summary["failed"] += 1
            else:
                summary["processed"] += 1
        except Exception as e:
            summary["failed"] += 1
            summary["errors"].append({"mid": mid, "err": str(e)})
    return summary


def run_parser_cycle(batch_size: int = 100, fan_out: bool = False) -> dict:
    """Daemon-style: process up to batch_size unparsed RTA emails."""
    import db_pg
    summary = {"processed": 0, "failed": 0}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "  AND COALESCE(classified_as,'') NOT IN ('rta','rta_parse_failed') "
                "ORDER BY received_at ASC LIMIT %s",
                ("%Real-Time Alert%", batch_size),
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
    ap = argparse.ArgumentParser(description="Real-Time Alert (RTA) parser")
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
