"""Investing Ideas Newsletter parser — weekly full roster + price levels.

Distinct from parser_investing_ideas (Leaderboard) and parser_ii_changes
(add/remove events). Body:
  "Long: DAR, SWBI, MUSA ...  Short: ROP, RBLX, ONON ...
   LEVELS Longs STOCK PREV.CLOSE ... DAR $63.12 $60.02 $65.91 ...
   Shorts ... ROP $343.32 $336.00 ..."
-> one (ticker, side, prev_close, range_low, range_high) row per name.

CLI: py parser_ii_newsletter.py [--message-id ID | --latest | --backfill | --probe ID]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date

import parser_research_common as prc

log = logging.getLogger(__name__)

SUBJECT_RE = re.compile(r"^\s*Investing\s+Ideas\s+Newsletter\b", re.I)
CLASSIFIED = "ii_newsletter"
_SUBJ_SQL = "subject ILIKE 'Investing Ideas Newsletter%'"

ROSTER_RE = re.compile(
    r"\bLong\s*:\s*(?P<long>.+?)\s*\bShort\s*:\s*(?P<short>.+?)"
    r"\s*(?:LEVELS|Longs\b|$)", re.I | re.S)
_TICK = r"[A-Z]{1,6}(?:\.[A-Z]{1,3})?"
TICK_RE = re.compile(rf"\b({_TICK})\b")
# "DAR $63.12 $60.02 $65.91" — ticker then first three $ values
LEVEL_ROW_RE = re.compile(
    rf"\b(?P<tk>{_TICK})\s+\$\s*(?P<prev>[\d,]+\.?\d*)\s+"
    r"\$\s*(?P<lo>[\d,]+\.?\d*)\s+\$\s*(?P<hi>[\d,]+\.?\d*)")
_STOP = {"LONG", "SHORT", "STOCK", "PREV", "TREND", "RANGES", "RANGE",
         "CHART", "LOW", "TOP", "END", "LEVELS", "LONGS", "SHORTS",
         "CLOSING", "PRICE", "AND", "THE"}


def is_ii_newsletter_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject or ""))


def _toks(blob: str) -> list[str]:
    out = []
    for m in TICK_RE.finditer(blob or ""):
        t = m.group(1).upper()
        if t not in _STOP and t not in out:
            out.append(t)
    return out


def _num(s):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def parse_ii_newsletter(body: str) -> list[dict]:
    body = body or ""
    side_map: dict[str, str] = {}
    order: list[str] = []
    rm = ROSTER_RE.search(body)
    if rm:
        for t in _toks(rm.group("long")):
            side_map.setdefault(t, "long")
            order.append(t)
        for t in _toks(rm.group("short")):
            side_map.setdefault(t, "short")
            order.append(t)

    # LEVELS price table; split Longs/Shorts to infer side for any name not
    # in the header roster.
    lv = body[rm.end():] if rm else body
    li = lv.lower()
    longs_at = li.find("longs")
    shorts_at = li.find("shorts")
    price_map: dict[str, tuple] = {}
    inferred: dict[str, str] = {}
    for m in LEVEL_ROW_RE.finditer(lv):
        tk = m.group("tk").upper()
        if tk in _STOP:
            continue
        price_map[tk] = (_num(m.group("prev")), _num(m.group("lo")),
                         _num(m.group("hi")))
        if tk not in side_map:
            if shorts_at != -1 and m.start() >= shorts_at:
                inferred[tk] = "short"
            elif longs_at != -1:
                inferred[tk] = "long"

    rows: list[dict] = []
    seen = set()
    for tk in order + [t for t in price_map if t not in side_map]:
        if tk in seen:
            continue
        seen.add(tk)
        side = side_map.get(tk) or inferred.get(tk)
        if not side:
            continue
        prev, lo, hi = price_map.get(tk, (None, None, None))
        rows.append({"ticker": tk, "side": side, "prev_close": prev,
                     "range_low": lo, "range_high": hi})
    return rows


def upsert_rows(rows, sd, feed_id, mid) -> dict:
    import db_pg
    w = f = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            try:
                cur.execute(
                    """INSERT INTO hedgeye_ii_newsletter
                       (signal_date,ticker,side,prev_close,range_low,range_high,
                        source_email_id,feed_item_id,parsed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT (signal_date,ticker,source_email_id)
                       DO UPDATE SET side=EXCLUDED.side,
                         prev_close=EXCLUDED.prev_close,
                         range_low=EXCLUDED.range_low,
                         range_high=EXCLUDED.range_high,
                         feed_item_id=EXCLUDED.feed_item_id, parsed_at=NOW()""",
                    (sd, r["ticker"], r["side"], r["prev_close"],
                     r["range_low"], r["range_high"], mid, feed_id),
                )
                w += 1
            except Exception as e:
                log.warning("ii_newsletter upsert %s: %s", r.get("ticker"), e)
                f += 1
        conn.commit()
    return {"written": w, "failed": f}


def _stamp(mid, c=CLASSIFIED):
    import db_pg
    try:
        db_pg.mark_email_classified(mid, c)
    except Exception as e:
        log.warning("stamp failed: %s", e)


def _note(rows, mid):
    try:
        import ticker_inventory
        n = 0
        for r in rows:
            n += ticker_inventory.note_tickers(
                [r["ticker"]], source="investing_ideas",
                position=r["side"], message_id=mid).get("noted", 0)
        return n
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def process_email(message_id: str, *, dry_run=False, fan_out=False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT subject,received_at,text_body,html_body "
                    "FROM hedgeye_emails_raw WHERE message_id=%s",
                    (message_id,))
        row = cur.fetchone()
    if not row:
        return {"error": "email not found"}
    subject, received_at, tb, hb = row
    if not is_ii_newsletter_subject(subject):
        return {"error": "subject not II Newsletter", "subject": subject}
    body = prc.text_of(tb, hb)
    rows = parse_ii_newsletter(body)
    sd = received_at.date() if received_at else date.today()
    summ = {"message_id": message_id, "subject": subject,
            "signal_date": str(sd), "rows_parsed": len(rows),
            "rows": rows, "dry_run": dry_run}
    if not rows:
        if not dry_run:
            _stamp(message_id, CLASSIFIED + "_parse_failed")
        summ["error"] = "no roster extracted"
        return summ
    if dry_run:
        return summ
    summ["upsert"] = upsert_rows(rows, sd, prc.feed_item_id(body), message_id)
    summ["noted_in_inventory"] = _note(rows, message_id)
    _stamp(message_id)
    return summ


def process_one(message_id: str, *, fan_out=False) -> dict:
    return process_email(message_id, fan_out=fan_out)


def process_latest(dry_run=False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT message_id FROM hedgeye_emails_raw WHERE "
                    f"{_SUBJ_SQL} ORDER BY received_at DESC LIMIT 1")
        r = cur.fetchone()
    return process_email(r[0], dry_run=dry_run) if r else {"error": "none"}


def _candidates(limit=None):
    import db_pg
    q = (f"SELECT message_id FROM hedgeye_emails_raw WHERE {_SUBJ_SQL} "
         f"AND COALESCE(classified_as,'') NOT IN "
         f"('{CLASSIFIED}','{CLASSIFIED}_parse_failed') "
         f"ORDER BY received_at ASC")
    if limit:
        q += f" LIMIT {int(limit)}"
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(q)
        return [r[0] for r in cur.fetchall()]


def backfill_all_unparsed(fan_out=False) -> dict:
    s = {"processed": 0, "failed": 0, "errors": []}
    for mid in _candidates():
        try:
            r = process_email(mid, fan_out=fan_out)
            s["failed" if r.get("error") else "processed"] += 1
        except Exception as e:
            s["failed"] += 1
            s["errors"].append({"mid": mid, "err": str(e)})
    return s


def run_parser_cycle(batch_size=100, fan_out=False) -> dict:
    s = {"processed": 0, "failed": 0}
    for mid in _candidates(batch_size):
        try:
            r = process_email(mid, fan_out=fan_out)
            s["failed" if r.get("error") else "processed"] += 1
        except Exception as e:
            s["failed"] += 1
            log.warning("cycle %s: %s", mid, e)
    return s


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Investing Ideas Newsletter parser")
    ap.add_argument("--message-id")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--probe")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
    if a.probe: r = process_email(a.probe, dry_run=True)
    elif a.message_id: r = process_email(a.message_id)
    elif a.latest: r = process_latest()
    elif a.backfill: r = backfill_all_unparsed()
    else: ap.print_help(); return 1
    print(json.dumps(r, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
