"""tools/parse_hedgeye_archive.py — roadmap §2 step 2: parse the archived
.eml files into the stated-view corpus.

2a  hedgeye_quad_stated — GIP-table rows (Themes/MidQ/Monitor), monthly
    path strings, Quad prose (Early Look / Macro Show / The Call).
    Regex-first; ambiguous prose is captured WITH ITS SNIPPET so every
    observation is auditable. inflation_nowcast from the Monthly
    Inflation Nowcast mails.
2b  hedgeye_risk_ranges backfill — parser_risk_range.parse_risk_range_email
    (the LIVE parser, verbatim) over the archived Risk Range mails;
    ON CONFLICT DO NOTHING so live-parsed rows always win; provenance in
    source_uid ('<folder>/<uid>').
2c  quad_nowcast_daily — latest stated Quad as-of each trading day
    (SPY px_daily calendar), forward-filled.

known_at = the email Date header, always.

    py tools/parse_hedgeye_archive.py [--limit N] [--product P]
"""
from __future__ import annotations

import argparse
import csv
import email
import email.utils
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()
from psycopg2.extras import execute_values  # noqa: E402

ARCH = REPO / "data" / "hedgeye_mail"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

GIP_RE = re.compile(
    r"([1-4])Q(\d{2})E?\b[^%\n]{0,50}?(\d{1,2}\.\d{2})\s*%[^%\n]{0,20}?"
    r"(\d{1,2}\.\d{2})\s*%[^\n]{0,30}?#?Quad\s*([1-4])", re.I)
GIP_SHORT_RE = re.compile(r"([1-4])Q(\d{2})E?\s*[=:\-]?\s*#?Quad\s*([1-4])", re.I)
MPATH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"[\s\-'/.]{0,3}(\d{2,4})?\s*[=:→\-]{0,2}>?\s*#?Q(?:uad)?\s*([1-4])", re.I)
PROSE_RE = re.compile(r"#?Quad\s*([1-4])", re.I)
QTR_NEAR_RE = re.compile(r"\b([1-4])Q(\d{2})E?\b")
NOWCAST_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"[\s\-'/.]{0,3}(\d{2,4})?[^%\n]{0,60}?(\d{1,2}\.\d{1,2})\s*%", re.I)


def body_text(p: Path) -> tuple[str, object]:
    msg = email.message_from_bytes(p.read_bytes())
    dt = email.utils.parsedate_to_datetime(msg.get("Date"))
    html, plain = "", ""
    for part in msg.walk():
        ct = part.get_content_type()
        if ct in ("text/html", "text/plain"):
            payload = part.get_payload(decode=True) or b""
            txt = payload.decode(part.get_content_charset() or "utf-8", "replace")
            if ct == "text/html":
                html += txt
            else:
                plain += txt
    if html:
        from parser_risk_range import extract_text_from_html
        return extract_text_from_html(html), dt, html, plain
    return plain, dt, html, plain


def _year(y, note_date):
    if not y:
        return note_date.year
    y = int(y)
    return 2000 + y if y < 100 else y


def parse_quads(text, note_date, product):
    """[(scope, period, quad, gdp, cpi, snippet)] — every observation."""
    out = []
    for m in GIP_RE.finditer(text):
        q, yy, g, i, quad = m.groups()
        out.append(("quarterly", f"{q}Q{yy}", int(quad), float(g), float(i),
                    text[max(0, m.start() - 20):m.end() + 20]))
    for m in GIP_SHORT_RE.finditer(text):
        q, yy, quad = m.groups()
        period = f"{q}Q{yy}"
        if not any(p == period for _s, p, *_ in out):
            out.append(("quarterly", period, int(quad), None, None,
                        text[max(0, m.start() - 40):m.end() + 40]))
    for m in MPATH_RE.finditer(text):
        mon, yy, quad = m.groups()
        y = _year(yy, note_date)
        period = f"{y}-{MONTHS[mon.lower()[:3]]:02d}"
        out.append(("monthly", period, int(quad), None, None,
                    text[max(0, m.start() - 40):m.end() + 40]))
    # prose fallback: bare Quad mentions with a nearby quarter token, else
    # attributed to the note's own month (per the brief)
    seen_spans = set()
    for m in PROSE_RE.finditer(text):
        span = (m.start() // 200)          # cheap dedupe by neighborhood
        if span in seen_spans:
            continue
        seen_spans.add(span)
        window = text[max(0, m.start() - 90):m.end() + 90]
        if GIP_RE.search(window) or GIP_SHORT_RE.search(window) \
                or MPATH_RE.search(window):
            continue                        # already captured structurally
        qn = QTR_NEAR_RE.search(window)
        if qn:
            scope, period = "quarterly", f"{qn.group(1)}Q{qn.group(2)}"
        else:
            scope, period = "monthly", f"{note_date.year}-{note_date.month:02d}"
        out.append((scope, period, int(m.group(1)), None, None, window))
    return out


def parse_nowcast(text, note_date):
    """[(month, cpi_est, scenario, snippet)] from an Inflation Nowcast."""
    out = []
    for m in NOWCAST_RE.finditer(text):
        mon, yy, val = m.groups()
        v = float(val)
        if not (0.0 < v < 15.0):
            continue
        y = _year(yy, note_date)
        window = text[max(0, m.start() - 60):m.end() + 40].lower()
        scen = ("upside" if "upside" in window else
                "downside" if "downside" in window else "base")
        out.append((date(y, MONTHS[mon.lower()[:3]], 1), v, scen,
                    text[max(0, m.start() - 40):m.end() + 20]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--product")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = list(csv.DictReader((ARCH / "manifest.csv").open(encoding="utf-8")))
    rows = [r for r in rows if r["file"]
            and (not args.product or r["product"] == args.product)]
    if args.limit:
        rows = rows[:args.limit]

    from parser_risk_range import parse_risk_range_email
    quad_rows, now_rows, rr_rows = [], [], []
    stats = {"rr_mails": 0, "rr_rows": 0, "quad_mails": 0, "quad_obs": 0,
             "nowcast_mails": 0, "nowcast_obs": 0, "errors": 0}
    for r in rows:
        p = ARCH / r["file"]
        uid = f"{r['folder']}/{r['uid']}"
        try:
            text, dt, html, plain = body_text(p)
            nd = date.fromisoformat(r["date"])
        except Exception as e:
            stats["errors"] += 1
            print(f"  ERROR {r['file']}: {e}")
            continue
        if r["product"] == "Risk Range":
            ranges, _changes = parse_risk_range_email(
                {"message_id": uid, "html_body": html, "text_body": plain,
                 "received_at": dt})
            stats["rr_mails"] += 1
            stats["rr_rows"] += len(ranges)
            for x in ranges:
                rr_rows.append((x["ticker"], x["signal_date"], x.get("trend"),
                                x.get("buy_trade"), x.get("sell_trade"),
                                x.get("prev_close"), x.get("description"),
                                uid))
        elif r["product"] == "Inflation Nowcast":
            obs = parse_nowcast(text, nd)
            stats["nowcast_mails"] += 1
            stats["nowcast_obs"] += len(obs)
            for month, v, scen, _sn in obs:
                now_rows.append((nd, month, v, scen, uid, dt))
        else:
            obs = parse_quads(text, nd, r["product"])
            if obs:
                stats["quad_mails"] += 1
                stats["quad_obs"] += len(obs)
            for scope, period, quad, g, i, sn in obs:
                quad_rows.append((nd, r["product"], scope, period, quad,
                                  g, i, uid, sn[:400], dt))
    print(f"parsed: {stats}")
    if args.dry_run:
        return 0

    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(rr_rows), 500):
            execute_values(cur,
                "INSERT INTO hedgeye_risk_ranges (ticker, signal_date, trend, "
                "buy_trade, sell_trade, prev_close, description, source_uid) "
                "VALUES %s ON CONFLICT (ticker, signal_date) DO NOTHING",
                rr_rows[i:i + 500], page_size=500)
            conn.commit()
        for i in range(0, len(quad_rows), 500):
            execute_values(cur,
                "INSERT INTO hedgeye_quad_stated (note_date, product, scope, "
                "period, quad, gdp_est, cpi_est, source_uid, snippet, known_at) "
                "VALUES %s ON CONFLICT DO NOTHING",
                quad_rows[i:i + 500], page_size=500)
            conn.commit()
        for i in range(0, len(now_rows), 500):
            execute_values(cur,
                "INSERT INTO inflation_nowcast (note_date, month, cpi_yoy_est, "
                "scenario, source_uid, known_at) VALUES %s "
                "ON CONFLICT DO NOTHING",
                now_rows[i:i + 500], page_size=500)
            conn.commit()
        # 2c: quad_nowcast_daily forward-fill over the SPY trading calendar
        cur.execute("SELECT bar_date FROM px_daily WHERE ticker='SPY' "
                    "ORDER BY bar_date")
        days = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT note_date, scope, period, quad FROM "
                    "hedgeye_quad_stated ORDER BY note_date")
        stated = cur.fetchall()
        daily = []
        mq = qq = src = None
        it = iter(stated)
        cur_row = next(it, None)
        for d in days:
            while cur_row and cur_row[0] <= d:
                _nd, scope, period, quad = cur_row
                # only statements about the CURRENT month/quarter move the dial
                if scope == "monthly" and period == f"{_nd.year}-{_nd.month:02d}":
                    mq, src = quad, _nd
                if scope == "quarterly":
                    qn = f"{(_nd.month - 1) // 3 + 1}Q{str(_nd.year)[2:]}"
                    if period == qn:
                        qq, src = quad, _nd
                cur_row = next(it, None)
            daily.append((d, mq, qq, src))
        execute_values(cur,
            "INSERT INTO quad_nowcast_daily (date, monthly_quad, "
            "quarterly_quad, source_note_date) VALUES %s "
            "ON CONFLICT (date) DO UPDATE SET monthly_quad=EXCLUDED.monthly_quad, "
            "quarterly_quad=EXCLUDED.quarterly_quad, "
            "source_note_date=EXCLUDED.source_note_date",
            daily, page_size=1000)
        conn.commit()
        cur.execute("SELECT count(*) FROM hedgeye_risk_ranges WHERE source_uid IS NOT NULL")
        print(f"hedgeye_risk_ranges backfilled rows: {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM hedgeye_quad_stated")
        print(f"hedgeye_quad_stated: {cur.fetchone()[0]}")
        cur.execute("SELECT count(*) FROM inflation_nowcast")
        print(f"inflation_nowcast: {cur.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
