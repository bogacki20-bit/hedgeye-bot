"""parser_t1a.py — Tier1 Alpha Market Situation Report -> t1a_snapshots.

Tier1 Alpha reaches us only through Hedgeye's daily Market Situation Report
email (~06:30 ET). The email carries a teaser; the full 9-page report is a
signed S3 PDF behind its "Click Here!" link (app.hedgeye.com/ms/msr_2-0...),
which needs Kris's logged-in session on his home connection (Cloudflare).
The PDF has NO text layer — every number is in an image — so values are read
off page images with Claude vision and sanity-checked before they're stored.

Two capture paths feed the same extractor:
  chrome_tab     — a scheduled Claude session drives Kris's own Chrome, zooms
                   pages 1/4/5 to PNGs (docs/T1A_CAPTURE.md), then:
                     py parser_t1a.py --images p1.png p4.png p5.png --date YYYY-MM-DD
  harvester_pdf  — hedgeye_links.py (bot Chrome on :9222) saves the PDF:
                     py parser_t1a.py --pdf MSR_2.0.pdf --date YYYY-MM-DD

Other CLI:
  py parser_t1a.py --link [--date D]     # today's full-report URL for the capture step
  py parser_t1a.py --show [--date D]     # stored row + T1A-vs-SG comparison

Design: docs/T1A_CAPTURE.md · build list #17.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html as htmlmod
import json
import logging
import os
import re
import sys

log = logging.getLogger(__name__)

T1A_MODEL = os.getenv("T1A_MODEL", "claude-sonnet-5")
# 1 key levels · 2 short-dated vol + event calendar · 3 dealer gamma · 4 systematic
# positioning · 5 index PV bands · 6 sector PV bands · 7 breadth (8-9 = glossary)
PAGES = (1, 2, 3, 4, 5, 6, 7)
RENDER_ZOOM = 2.0

STATUS_SETS = {
    "gamma_exposure":       {"Positive", "Neutral", "Negative"},
    "systematic_flow_risk": {"Bullish", "Neutral", "Bearish"},
    "pv_band_rr":           {"Long", "Neutral", "Short"},
    "strategic_allocation": {"Risk On", "Neutral", "Risk Off"},
}
# SPX levels that must sit near spot — a misread digit lands far outside.
NEAR_SPOT = ("upper_pv_band", "lower_pv_band", "gex_flip",
             "resistance_strike", "focal_strike", "support_strike")
NEAR_SPOT_PCT = 8.0

EXTRACT_PROMPT = """These images are pages of a Tier1 Alpha "Market Situation Report".
Read the values EXACTLY as printed. Return ONLY one JSON object, no prose:

{
 "report_date": "YYYY-MM-DD",          // big date in the page-1 header
 "data_date": "YYYY-MM-DD",            // "Date: YYYY/MM/DD" printed under the charts
 "gamma_exposure": "Positive|Neutral|Negative",        // highlighted cell, status grid
 "systematic_flow_risk": "Bullish|Neutral|Bearish",
 "pv_band_rr": "Long|Neutral|Short",
 "strategic_allocation": "Risk On|Neutral|Risk Off",
 "spx_last": number, "upper_pv_band": number, "lower_pv_band": number,
 "upside_risk_pct": number, "downside_risk_pct": number,   // e.g. 1.49 and -1.18
 "gex_throttle": number, "gex_flip": number, "implied_move_pct": number,
 "resistance_strike": number, "focal_strike": number, "support_strike": number,
 "sys_positioning_z": number,          // LAST value of the blue Systematic Positioning
                                       // Index line (right end), read off the y-axis
 "vc_flow_by_move": {"-5": n, "-4": n, "-3": n, "-2": n, "-1": n, "0": n,
                     "1": n, "2": n, "3": n, "4": n, "5": n},
                                       // "Est. Chg $bn" row above the Vol Control
                                       // Implied Rebalancing Flow chart
 "ndx_zero_gamma": number, "ndx_upper_pv": number, "ndx_lower_pv": number,
 "rut_zero_gamma": number, "rut_upper_pv": number, "rut_lower_pv": number,
                                       // legends of the NDX / Russell 2000
                                       // Probable Volatility charts (p5)
 "strikes_in_play": {"SPX": [n...], "NDX": [n...], "RUT": [n...]},   // p5 boxes
 "term_structure": [{"expiry": "YYYY-MM-DD", "iv": n}],   // p2 chart: every labeled point
 "event_calendar": [{"date": "YYYY-MM-DD", "event": s, "estimate": n|null,
                     "previous": n|null, "impact": "low|medium|high", "call_iv": n,
                     "put_iv": n, "spx_iv": n, "expected_move_pct": n,
                     "pc_vol": n, "pc_oi": n}],            // p2 table, every row
 "gex_per_1pct_bn": number,            // p3 legend "GEX Per 1%/Move: $5B" -> 5
 "rv_10d": number,                     // p3 throttle box "10-Day rVol"
 "rv_1m_t1a": number, "rv_3m_t1a": number,   // p4 1m/3m realized lines, right end (approx)
 "vc_notional_bn": number,             // p4 Vol Control implied notional, right end (approx, $bn)
 "cta_position": number,               // p4 CTA positioning blue line, right end (approx)
 "rv_dropoff": [{"t": 1, "d1m": "YYYY-MM-DD", "drop1m": n, "d3m": "YYYY-MM-DD", "drop3m": n}],
                                       // p4 "SPX Realized Vol Data In Sample" table T+1..T+10
 "sector_bands": [{"asset": s, "last": n, "lower": n, "upper": n, "upside_pct": n,
                   "downside_pct": n, "spread_pct": n, "rvol_1m": n, "beta_2y": n}],  // p6 table
 "stocks_up": int, "stocks_down": int, "spx_daily_pct": n, "avg_return_pct": n,
 "avg_gain_pct": n, "avg_decline_pct": n,                // p7 MBAD box + legend
 "breadth_20d_pct": n, "breadth_50d_pct": n, "breadth_200d_pct": n,
                                       // p7 blue breadth lines, right end, RIGHT axis 0-100 (approx)
 "impact_leaders": [{"side": "pos|neg", "ticker": s, "ret_pct": n, "impact_pct": n, "weight_pct": n}],
                                       // p7 relative-impact table, all 20 rows
 "approx_fields": ["sys_positioning_z", ...]   // every field read off a chart line
}
Percent fields are plain numbers (1.49 means 1.49%). Use null for anything
not visible in these images — never guess a number."""


# ─────────────────────────── extraction ───────────────────────────

def render_pdf_pages(pdf_path: str, pages=PAGES) -> list[bytes]:
    import pymupdf
    doc = pymupdf.open(pdf_path)
    out = []
    for n in pages:
        if n - 1 < len(doc):
            pix = doc[n - 1].get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM))
            out.append(pix.tobytes("png"))
    return out


def extract(images: list[bytes]) -> dict:
    """Claude vision -> dict. Raises on unparseable output."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    content = [{"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.b64encode(b).decode()}} for b in images]
    content.append({"type": "text", "text": EXTRACT_PROMPT})
    resp = client.messages.create(model=T1A_MODEL, max_tokens=2000,
                                  messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"extractor returned no JSON: {text[:200]!r}")
    return json.loads(m.group(0))


def validate(d: dict) -> tuple[list[str], list[str]]:
    """(hard_errors, warnings). Hard errors block the write."""
    hard, warn = [], []
    for k, allowed in STATUS_SETS.items():
        if d.get(k) is not None and d[k] not in allowed:
            hard.append(f"{k}={d[k]!r} not in {sorted(allowed)}")
    spot = d.get("spx_last")
    if not isinstance(spot, (int, float)) or not 1000 < spot < 50000:
        hard.append(f"spx_last={spot!r} implausible")
    else:
        for k in NEAR_SPOT:
            v = d.get(k)
            if v is None:
                warn.append(f"{k} missing")
            elif abs(v / spot - 1) * 100 > NEAR_SPOT_PCT:
                hard.append(f"{k}={v} is >{NEAR_SPOT_PCT:.0f}% from spot {spot}")
        lo, hi = d.get("lower_pv_band"), d.get("upper_pv_band")
        if lo is not None and hi is not None and not lo < hi:
            hard.append(f"PV band inverted ({lo} / {hi})")
    th = d.get("gex_throttle")
    if th is not None and not -150 <= th <= 60:
        hard.append(f"gex_throttle={th} outside the chart's -105..35 range")
    z = d.get("sys_positioning_z")
    if z is not None and not -4 <= z <= 4:
        warn.append(f"sys_positioning_z={z} outside ±4 — dropped")
        d["sys_positioning_z"] = None
    vc = d.get("vc_flow_by_move") or {}
    if vc and set(vc) != {str(i) for i in range(-5, 6)}:
        warn.append(f"vc_flow_by_move keys {sorted(vc)} incomplete")
    for k, lo_, hi_ in (("rv_10d", 2, 150), ("rv_1m_t1a", 2, 150), ("rv_3m_t1a", 2, 150),
                        ("breadth_20d_pct", 0, 100), ("breadth_50d_pct", 0, 100),
                        ("breadth_200d_pct", 0, 100)):
        v = d.get(k)
        if v is not None and not lo_ <= v <= hi_:
            warn.append(f"{k}={v} outside {lo_}-{hi_} — dropped")
            d[k] = None
    up, dn = d.get("stocks_up"), d.get("stocks_down")
    if up is not None and dn is not None and not 480 <= up + dn <= 505:
        warn.append(f"stocks up+down={up + dn}, expected ~500")
    for row in d.get("term_structure") or []:
        if not 3 <= (row.get("iv") or 0) <= 150:
            hard.append(f"term_structure iv {row} implausible")
            break
    for k in ("report_date", "data_date"):
        try:
            if d.get(k):
                dt.date.fromisoformat(d[k])
        except ValueError:
            hard.append(f"{k}={d[k]!r} not a date")
    return hard, warn


# ─────────────────────────── persistence ───────────────────────────

COLS = ("data_date", "gamma_exposure", "systematic_flow_risk", "pv_band_rr",
        "strategic_allocation", "spx_last", "upper_pv_band", "lower_pv_band",
        "upside_risk_pct", "downside_risk_pct", "gex_throttle", "gex_flip",
        "implied_move_pct", "resistance_strike", "focal_strike", "support_strike",
        "sys_positioning_z", "vc_flow_by_move", "ndx_zero_gamma", "ndx_upper_pv",
        "ndx_lower_pv", "rut_zero_gamma", "rut_upper_pv", "rut_lower_pv",
        # migration 114 — the rest of the report
        "term_structure", "event_calendar", "gex_per_1pct_bn", "rv_10d",
        "rv_1m_t1a", "rv_3m_t1a", "vc_notional_bn", "cta_position", "rv_dropoff",
        "strikes_in_play", "sector_bands", "stocks_up", "stocks_down",
        "spx_daily_pct", "avg_return_pct", "avg_gain_pct", "avg_decline_pct",
        "breadth_20d_pct", "breadth_50d_pct", "breadth_200d_pct", "impact_leaders")
JSON_COLS = {"vc_flow_by_move", "term_structure", "event_calendar", "rv_dropoff",
             "strikes_in_play", "sector_bands", "impact_leaders"}


def upsert(d: dict, report_date: str, capture_path: str,
           source_email_id: str | None) -> None:
    import db_pg
    from psycopg2.extras import Json
    vals = [Json(d[c]) if c in JSON_COLS and d.get(c) is not None
            else d.get(c) for c in COLS]
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in COLS)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO t1a_snapshots (report_date, source_email_id, capture_path, "
            f"{', '.join(COLS)}, raw) VALUES (%s, %s, %s, "
            f"{', '.join(['%s'] * len(COLS))}, %s) "
            f"ON CONFLICT (report_date) DO UPDATE SET source_email_id=EXCLUDED.source_email_id, "
            f"capture_path=EXCLUDED.capture_path, {sets}, raw=EXCLUDED.raw, parsed_at=now()",
            [report_date, source_email_id, capture_path, *vals, Json(d)])
        conn.commit()


def ingest(images: list[bytes] | None, report_date: str | None, capture_path: str,
           source_email_id: str | None = None, parsed: dict | None = None) -> dict:
    """images -> API extraction; or `parsed` = values already read off the
    pages (the scheduled Claude session reads them itself — no API key)."""
    d = parsed if parsed is not None else extract(images)
    hard, warn = validate(d)
    rd = report_date or d.get("report_date")
    if report_date and d.get("report_date") and d["report_date"] != report_date:
        hard.append(f"page header says {d['report_date']}, expected {report_date}")
    if hard:
        log.warning("t1a: NOT stored — %s", hard)
        return {"stored": False, "errors": hard, "warnings": warn, "parsed": d}
    upsert(d, rd, capture_path, source_email_id or _email_id_for(rd))
    return {"stored": True, "report_date": rd, "warnings": warn, "parsed": d}


# ─────────────────────────── email link / lookup ───────────────────────────

def _msr_email(day: dt.date):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT message_id, html_body FROM hedgeye_emails_raw "
            "WHERE subject ILIKE 'MARKET SITUATION REPORT:%%' "
            "  AND subject NOT ILIKE '%%WATCH%%' "
            "  AND (received_at AT TIME ZONE 'America/New_York')::date = %s "
            "ORDER BY received_at DESC LIMIT 1", (day,))
        return cur.fetchone()


def _t1a_row_date(day: dt.date) -> dt.date | None:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT report_date FROM t1a_snapshots WHERE report_date=%s", (day,))
        row = cur.fetchone()
    return row[0] if row else None


def _email_id_for(report_date: str | None) -> str | None:
    if not report_date:
        return None
    row = _msr_email(dt.date.fromisoformat(report_date))
    return row[0] if row else None


def full_report_link(day: dt.date) -> str | None:
    """The email's 'Click Here!' target, resolved only as far as
    app.hedgeye.com — the rest of the hop needs the logged-in browser."""
    row = _msr_email(day)
    if not row:
        return None
    import httpx
    from bs4 import BeautifulSoup
    for a in BeautifulSoup(row[1] or "", "html.parser").find_all("a"):
        if not re.search(r"click here", a.get_text(" ", strip=True), re.I):
            continue
        url = htmlmod.unescape(a.get("href", ""))
        for _ in range(6):                     # follow tracking hops by hand
            if "app.hedgeye.com/ms/" in url:
                return url
            r = httpx.head(url, follow_redirects=False, timeout=15)
            nxt = r.headers.get("location")
            if not nxt:
                break
            url = nxt
    return None


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--images", nargs="+")
    g.add_argument("--pdf")
    g.add_argument("--json", help="values already read off the pages (EXTRACT_PROMPT schema)")
    g.add_argument("--render", metavar="PDF", help="write pages 1/4/5 as PNGs next to the PDF")
    g.add_argument("--link", action="store_true")
    g.add_argument("--show", action="store_true")
    g.add_argument("--send", action="store_true",
                   help="send the full T1A card (block + every table) to Telegram")
    ap.add_argument("--date", help="report date YYYY-MM-DD (default: today ET)")
    ap.add_argument("--dry-run", action="store_true", help="extract + validate, no write")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import db_pg
    db_pg._load_dotenv_fallback()
    from zoneinfo import ZoneInfo
    day = (dt.date.fromisoformat(args.date) if args.date
           else dt.datetime.now(ZoneInfo("America/New_York")).date())

    if args.link:
        link = full_report_link(day)
        print(link or f"NO MSR email with a full-report link for {day}")
        return 0 if link else 1
    if args.show:
        from tools.t1a_vs_sg import build_block
        print(build_block(day))
        return 0
    if args.send:
        # The trading-day card (desk 9/26: "the full tier one for trading that
        # day"). The 9:40 desk note is capped at 4,000 chars, so the full
        # report goes out as its own message; notifier chunks it at 4,096.
        from tools.t1a_vs_sg import build_block, full_detail
        if _t1a_row_date(day) != day:
            print(f"NOT sent: no T1A report stored for {day}")
            return 1
        block = build_block(day).split("\n")
        body = "\n".join(block[1:] + [""] + full_detail(day))
        from parser_tms_deck import load_telegram_env
        load_telegram_env()
        from notifier import send_telegram
        ok = send_telegram(block[0], body)
        print("sent" if ok else "NOT sent (Telegram error, see log)")
        return 0 if ok else 1

    if args.render:
        import pathlib
        base = pathlib.Path(args.render)
        for n, png in zip(PAGES, render_pdf_pages(args.render)):
            out = base.with_name(f"{base.stem}_p{n}.png")
            out.write_bytes(png)
            print(out)
        return 0
    if args.json:
        d = json.loads(open(args.json, encoding="utf-8").read())
        if args.dry_run:
            hard, warn = validate(d)
            print(json.dumps({"errors": hard, "warnings": warn}, indent=1))
            return 1 if hard else 0
        r = ingest(None, day.isoformat(), "chrome_tab", parsed=d)
        print(json.dumps(r, indent=1, default=str))
        return 0 if r["stored"] else 1

    if args.pdf:
        images, path = render_pdf_pages(args.pdf), "harvester_pdf"
    else:
        images, path = [open(p, "rb").read() for p in args.images], "chrome_tab"
    if args.dry_run:
        d = extract(images)
        hard, warn = validate(d)
        print(json.dumps({"parsed": d, "errors": hard, "warnings": warn}, indent=1))
        return 1 if hard else 0
    r = ingest(images, day.isoformat(), path)
    print(json.dumps(r, indent=1, default=str))
    return 0 if r["stored"] else 1


if __name__ == "__main__":
    sys.exit(_cli())
