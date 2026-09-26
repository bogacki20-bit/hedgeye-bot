"""parser_tms_deck.py — The Macro Show slide deck (the whole daily pack) -> tms_decks.

The ~08:30 "THE MACRO SHOW: <date> - Access Show, Slide Deck & Top 3 Things"
email carries a "CLICK HERE TO ACCESS TODAY'S SLIDES" link
(app.hedgeye.com/mu/tms_<hosts>_<m-d-yyyy>) that redirects a logged-in browser
to a signed S3 PDF (5-minute expiry). The deck is posted after the 9:00 show.

Section order never changes; lengths do. Divider pages split it:
  cover · disclaimer · Top-3 divider · Top 3 pages · OUR PROCESS · CHART
  CALLOUTS · MACRO GRIND · VOLATILITY · HEDGEYE EVENTS
Top 3 and Our Process have a text layer (captured here, no vision). Every
table is an image, so --prep renders the pages that need reading and writes a
skeleton JSON; a scheduled Claude session reads the PNGs and fills it in
(SCHEMA below); --json validates and stores.

CLI:
  py parser_tms_deck.py --link [--date D]           # today's /mu/ slides URL
  py parser_tms_deck.py --prep PDF [--date D]       # split, text, PNGs, skeleton
  py parser_tms_deck.py --zoom PDF PAGE             # one page at 3x for dense tables
  py parser_tms_deck.py --json FILE [--date D]      # validate + store
  py parser_tms_deck.py --show [--date D]

Design: docs/TMS_DECK_CAPTURE.md · migration 115.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as htmlmod
import json
import logging
import pathlib
import re
import sys

log = logging.getLogger(__name__)

RENDER_ZOOM = 2.0
ZOOM_HI = 3.0
PRODUCT = "Macro Show Deck"

DIVIDERS = {"OUR PROCESS": "process", "CHART CALLOUTS": "callouts",
            "MACRO GRIND": "grind", "VOLATILITY": "volatility"}

# Fixed Macro Grind / Volatility page titles -> JSON key. Pages carrying only
# a chart + "Series" box (yield curve, GS baskets, MOVE, HY/FX vol, BBB) go
# to "series"; a title nobody knows yet goes to "extra_pages".
TITLE_KEYS = (
    ("sector relative performance", "sector_rel"),
    ("sector performance", "sector_perf"),
    ("market share volume", "volume"),
    ("style factor performance", "style_factors"),
    ("key $usd correlations", "usd_corr"),
    ("cftc", "cftc"),
    ("implied & realized volatility", "ivol"),
    ("macro volatility", "macro_vol"),
    ("u.s. treasury yield curve", "series"),
    ("goldman sachs", "series"),
    ("bond volatility", "series"),
    ("high yield market volatility", "series"),
    ("global currency volatility", "series"),
    ("corporate spread", "series"),
)
TABLE_KEYS = ("sector_perf", "sector_rel", "volume", "style_factors", "usd_corr",
              "cftc", "ivol", "macro_vol", "series", "callouts", "extra_pages")

SCHEMA = """Fill every null key of the skeleton from the listed page PNGs.
Copy printed numbers EXACTLY; percents as plain numbers (-5.38 means -5.38%);
"(3,846)" -> -3846; "#VALUE!" or "-" -> null. Never guess: null what you can't see.
Chart-line reads (no printed number) go in approx_fields as "key[i].field".

 "data_date": "YYYY-MM-DD"     // the close the Macro Grind tables use
                               // (volume page "9/24 vs.", page header date)
 "quad_model": {               // Our Process "What Are The Quads?" page
   "updated": "YYYY-MM-DD",    // orange "Updated: M/D/YYYY" bar
   "quarters": [{"q": "3Q26", "kind": "actual|estimate",   // every table column;
                 "gdp_qoq_saar": n, "gdp_yoy": n, "gdp_base_2y": n,  // right-hand block
                 "cpi_yoy": n, "cpi_base_2y": n}],                   // = estimate
   "path": [{"q": "3Q26", "quad": 1-4, "estimate": bool, "shallow": bool}]
                               // every labeled box on the chart; quadrant from
                               // position (x = CPI 1st diff, y = GDP 1st diff);
                               // shallow = inside the light-blue band; estimate =
                               // label ends in E; "2Q26 E/A" -> q "2Q26", estimate true
 }
 "sector_perf": [{"sector": s, "ticker": s, "price": n, "d1": n, "mtd": n, "qtd": n, "ytd": n}]
                               // all 11 sectors + the S&P 500 row (ticker "SPX")
 "sector_rel":  [ same shape, 11 rows ]   // relative to S&P 500
 "volume": [{"metric": s, "vs_prior_day": n, "vs_1m": n, "vs_3m": n, "vs_1y": n}]
 "style_factors": [{"group": s, "factor": s, "d1": n, "w1": n, "m1": n, "m3": n, "m6": n, "ytd": n}]
 "usd_corr": [{"metric": s, "d15": n, "d30": n, "d90": n, "d120": n, "d180": n,
               "hi_52w": n, "lo_52w": n, "pct_pos": n, "pct_neg": n}]
 "cftc": [{"group": "equities|rates|currencies|commodities", "metric": s, "latest": n,
           "wow": n, "avg_3m": n, "avg_6m": n, "avg_1y": n, "max_3y": n, "min_3y": n,
           "z_1y": n, "z_3y": n}]              // every row; z "0.62X" -> 0.62
 "ivol": [{"section": s, "name": s, "ticker": s, "ytd_ret": n, "ivol_prem": n,
           "prem_yday": n, "prem_1w": n, "prem_1m": n, "ttm_z": n, "z_3y": n,
           "rv_mm": n, "rv_pct_10y": n, "iv_mm": n, "iv_pct_10y": n}]
                               // Implied & Realized Volatility, every row
                               // (dense: use --zoom on this page)
 "macro_vol": [{"index": "VIX|NazVol|RVX|GVZ|OVX", "last": n, "d1": n, "w1": n,
                "m1": n, "label": s|null}]     // the boxes; d1/w1/m1 = "1 Day/1 Week/1 Month" ago
 "series": [{"page_title": s, "series": s, "last": n, "d1_ago": n, "w1_ago": n,
             "w4_ago": n, "ytd": n|null, "dod": s, "dod_pct": n, "wow": s, "wow_pct": n,
             "mom": s, "mom_pct": n, "ytd_pct": n|null}]
                               // the small "Series" box on every chart page
                               // (yield curve has 3 rows); deltas as printed,
                               // e.g. "-2 bps"
 "callouts": [{"page": n, "title": s, "source": s, "levels": {label: n},
               "summary": s}]  // every Chart Callouts page: printed numbers
                               // that matter (Risk Range Trade/Trend, last,
                               // % moves, table rows as a list under "rows"),
                               // one-sentence summary of what it shows
 "extra_pages": [{"page": n, "title": s, "data": {...}}]  // anything _pages
                               // marks key "extra_pages"
 "approx_fields": [...]"""


# ─────────────────────────── splitting ───────────────────────────

def _norm(t: str) -> str:
    return " ".join((t or "").split())


def _title_of(text: str) -> tuple[str | None, str | None]:
    """'The Macro Show © Hedgeye Risk Management LLC Sector Performance
    September 24, 2026 DATA SOURCE: ...' -> ('Sector Performance', '2026-09-24')."""
    m = re.search(r"LLC\.?\s+(.+?)\s+((?:January|February|March|April|May|June|July|"
                  r"August|September|October|November|December)\s+\d{1,2},\s+\d{4})", text)
    if not m:
        return None, None
    try:
        d = dt.datetime.strptime(m.group(2), "%B %d, %Y").date().isoformat()
    except ValueError:
        d = None
    return m.group(1).strip(), d


def _key_for(title: str | None) -> str:
    t = (title or "").lower()
    for needle, key in TITLE_KEYS:
        if needle in t:
            return key
    return "extra_pages"


def split_deck(pdf_path: str) -> dict:
    """Page-by-page map of the deck from its text layer."""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    pages, section = [], "front"
    for i, pg in enumerate(doc, 1):
        t = _norm(pg.get_text())
        up = t.upper()
        kind = section
        if i == 1:
            kind = "cover"
        elif "DISCLAIMER" in up and len(t) > 1000:
            kind = "disclaimer"
        elif up in DIVIDERS:
            section = DIVIDERS[up]
            kind = "divider"
        elif "HEDGEYE EVENTS" in up or section == "events":
            section = kind = "events"
        elif section == "front":
            # first short page after the disclaimer lists the Top 3 topics
            kind, section = "top3_divider", "top3"
        pages.append({"page": i, "section": kind, "text": t})
    return {"page_count": len(doc), "pages": pages}


def _sha(t: str) -> str:
    return hashlib.sha1(t.encode()).hexdigest()[:12]


def _last_process_hashes() -> dict[str, str]:
    """{text-sha: page} of the last stored deck's Our Process pages."""
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT process_text FROM tms_decks WHERE process_text IS NOT NULL "
                        "ORDER BY show_date DESC LIMIT 1")
            row = cur.fetchone()
    except Exception as e:                       # table missing / offline: render all
        log.warning("tms: no previous process hashes (%s)", e)
        return {}
    return {v["sha"]: k for k, v in (row[0] if row else {}).items()}


def prep(pdf_path: str, show_date: str) -> pathlib.Path:
    """Split, pull text, render the pages that need reading, write the
    skeleton JSON next to the PDF. Returns the skeleton path."""
    import pymupdf
    pdf = pathlib.Path(pdf_path)
    split = split_deck(pdf_path)
    doc = pymupdf.open(pdf_path)
    known = _last_process_hashes()

    sections: dict[str, list[int]] = {}
    for p in split["pages"]:
        sections.setdefault(p["section"], []).append(p["page"])

    topics = next((p["text"] for p in split["pages"] if p["section"] == "top3_divider"), "")
    top3_pages = [q for q in split["pages"] if q["section"] == "top3"]
    words = topics.split()                       # "RATES RUSSELL FACTORS"
    top3 = []
    for n, p in enumerate(top3_pages):
        m = re.match(r"(\d)\s+([A-Z][A-Z$ &/]+?)\s+(?=[A-Z][a-z])", p["text"])
        topic = m.group(2).strip() if m else (
            words[n] if len(words) == len(top3_pages) else None)
        top3.append({"n": n + 1, "page": p["page"], "topic": topic, "text": p["text"]})

    to_read, process_text, grind_dates = [], {}, []
    for p in split["pages"]:
        n, sec, t = p["page"], p["section"], p["text"]
        if sec == "process":
            process_text[str(n)] = {"sha": _sha(t), "text": t}
            if "Updated:" in t:
                to_read.append({"page": n, "key": "quad_model", "title": "What Are The Quads? (live model)"})
            elif known and _sha(t) not in known:
                to_read.append({"page": n, "key": "extra_pages", "title": "Our Process page CHANGED"})
        elif sec == "callouts":
            to_read.append({"page": n, "key": "callouts", "title": t[:120]})
        elif sec in ("grind", "volatility"):
            title, d = _title_of(t)
            if d:
                grind_dates.append(d)
            to_read.append({"page": n, "key": _key_for(title), "title": title or t[:120]})

    out_dir = pdf.parent
    for r in to_read:
        png = out_dir / f"tms_p{r['page']:02d}.png"
        png.write_bytes(doc[r["page"] - 1].get_pixmap(
            matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)).tobytes("png"))
        r["png"] = str(png)

    keys_needed = sorted({r["key"] for r in to_read})
    skel = {
        "show_date": show_date,
        "deck_file": pdf.name,
        "page_count": split["page_count"],
        "sections": sections,
        "top3_topics": topics,
        "top3": top3,
        "process_text": process_text,
        "header_date": max(set(grind_dates), key=grind_dates.count) if grind_dates else None,
        "_pages": to_read,
        "_instructions": SCHEMA,
        "data_date": None,
        "quad_model": None,
        **{k: None for k in TABLE_KEYS if k in keys_needed},
        "approx_fields": [],
    }
    path = out_dir / "tms_deck.json"
    path.write_text(json.dumps(skel, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


# ─────────────────────────── validation ───────────────────────────

SECTOR_TICKERS = {"XLY", "XLF", "XLV", "XLK", "XLP", "XLI", "XLB", "XLE", "XLU", "XLRE", "XLC"}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def implied_quads(quarters: list[dict]) -> dict[str, int]:
    """Quad per quarter from the GIP table itself: sign of the quarter-over-
    quarter change in GDP YoY (y) and CPI YoY (x). Needs table order."""
    out = {}
    for prev, cur in zip(quarters, quarters[1:]):
        g0, g1, c0, c1 = (prev.get("gdp_yoy"), cur.get("gdp_yoy"),
                          prev.get("cpi_yoy"), cur.get("cpi_yoy"))
        if not all(_num(v) for v in (g0, g1, c0, c1)):
            continue
        dg, dc = g1 - g0, c1 - c0
        if dg == 0 or dc == 0:
            continue                             # on an axis: let the chart decide
        out[cur["q"]] = (1 if dc < 0 else 2) if dg > 0 else (3 if dc > 0 else 4)
    return out


def validate(d: dict) -> tuple[list[str], list[str]]:
    """(hard_errors, warnings). Hard errors block the write."""
    hard, warn = [], []
    needed = {p["key"] for p in d.get("_pages") or []}
    for k in sorted(needed):
        if not d.get(k):
            hard.append(f"{k} is empty but the deck has a page for it")

    try:
        sd = dt.date.fromisoformat(d["show_date"])
        dd = dt.date.fromisoformat(d["data_date"]) if d.get("data_date") else None
        if dd is None:
            warn.append("data_date missing")
        elif not sd - dt.timedelta(days=5) <= dd <= sd:
            hard.append(f"data_date {dd} not within 5 days before show_date {sd}")
        elif dd == sd:
            warn.append(f"data_date == show_date {sd} (deck headers are sometimes stale)")
    except (KeyError, ValueError) as e:
        hard.append(f"bad date: {e}")

    qm = d.get("quad_model")
    if qm:
        try:
            dt.date.fromisoformat(qm.get("updated") or "")
        except ValueError:
            hard.append(f"quad_model.updated={qm.get('updated')!r} not a date")
        for q in qm.get("quarters") or []:
            if not re.fullmatch(r"[1-4]Q\d\d", q.get("q") or ""):
                hard.append(f"quad_model quarter label {q.get('q')!r}")
            for f in ("gdp_qoq_saar", "gdp_yoy", "cpi_yoy"):
                v = q.get(f)
                if v is not None and not (_num(v) and -15 < v < 20):
                    hard.append(f"quad_model {q.get('q')} {f}={v!r} implausible")
        path = qm.get("path") or []
        if not path:
            hard.append("quad_model.path empty")
        implied = implied_quads(qm.get("quarters") or [])
        for p in path:
            if p.get("quad") not in (1, 2, 3, 4):
                hard.append(f"quad_model.path {p} quad not 1-4")
            elif p.get("q") in implied and implied[p["q"]] != p["quad"]:
                hard.append(f"quad_model.path {p['q']} read as Quad {p['quad']} but the table "
                            f"implies Quad {implied[p['q']]} (misread cell or box?)")

    def rows(k, n_min, n_max=None):
        r = d.get(k)
        if r is None:
            return []
        if not isinstance(r, list):
            hard.append(f"{k} is not a list")
            return []
        if len(r) < n_min or (n_max and len(r) > n_max):
            hard.append(f"{k} has {len(r)} rows, expected {n_min}-{n_max or 'more'}")
        return r

    sp = rows("sector_perf", 12, 12)
    if sp:
        tick = {r.get("ticker") for r in sp}
        if tick != SECTOR_TICKERS | {"SPX"}:
            hard.append(f"sector_perf tickers {sorted(t for t in tick if t)} != 11 sectors + SPX")
        for r in sp:
            if _num(r.get("d1")) and abs(r["d1"]) > 15:
                hard.append(f"sector_perf {r.get('ticker')} d1={r['d1']} implausible")
    sr = rows("sector_rel", 11, 11)
    if sr and {r.get("ticker") for r in sr} != SECTOR_TICKERS:
        hard.append("sector_rel tickers are not the 11 sector SPDRs")
    if sp and sr:
        px = {r.get("ticker"): r.get("price") for r in sp}
        bad = [r["ticker"] for r in sr if px.get(r.get("ticker")) != r.get("price")]
        if bad:
            warn.append(f"sector_rel prices differ from sector_perf for {bad}")
    rows("style_factors", 14, 14)
    rows("usd_corr", 3)
    for r in rows("cftc", 20):
        for f in ("z_1y", "z_3y"):
            if _num(r.get(f)) and abs(r[f]) > 6:
                hard.append(f"cftc {r.get('metric')} {f}={r[f]} implausible")
    iv = rows("ivol", 30)
    if iv:
        tickers = [r["ticker"] for r in iv if r.get("ticker")]
        if len(set(tickers)) < len(tickers):
            warn.append("ivol has duplicate tickers")
    for r in rows("macro_vol", 3):
        if r.get("index") == "VIX" and not (_num(r.get("last")) and 5 < r["last"] < 90):
            hard.append(f"VIX last={r.get('last')!r} implausible")
    for r in rows("series", 1):
        last, w1 = r.get("last"), r.get("w1_ago")
        if _num(last) and _num(w1) and w1 and abs(last / w1 - 1) > 0.6:
            warn.append(f"series {r.get('series')} last {last} vs 1wk {w1} moved >60%")
    for r in rows("callouts", 1):
        if not r.get("title"):
            warn.append(f"callout p{r.get('page')} has no title")
    return hard, warn


# ─────────────────────────── persistence ───────────────────────────

JSON_COLS = ("sections", "top3", "process_text", "quad_model", *TABLE_KEYS)


def _email_row(day: dt.date):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT message_id, html_body, received_at FROM hedgeye_emails_raw "
            "WHERE subject ILIKE 'THE MACRO SHOW:%%Slide Deck%%' "
            "  AND (received_at AT TIME ZONE 'America/New_York')::date = %s "
            "ORDER BY received_at DESC LIMIT 1", (day,))
        return cur.fetchone()


def upsert(d: dict) -> int:
    """Write the deck row + the quad model's estimate quarters to
    hedgeye_quad_stated. Returns the number of stated-quad rows written."""
    import db_pg
    from psycopg2.extras import Json
    sd = dt.date.fromisoformat(d["show_date"])
    em = _email_row(sd)
    qm = d.get("quad_model") or {}
    cols = ("data_date", "deck_file", "source_email_id", "page_count",
            "quad_model_updated", *JSON_COLS)
    vals = {"data_date": d.get("data_date"), "deck_file": d.get("deck_file"),
            "source_email_id": em[0] if em else None, "page_count": d.get("page_count"),
            "quad_model_updated": qm.get("updated")}
    for c in JSON_COLS:
        vals[c] = Json(d[c]) if d.get(c) is not None else None
    raw = {k: v for k, v in d.items() if k != "_instructions"}
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
    n_stated = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO tms_decks (show_date, {', '.join(cols)}, raw) "
            f"VALUES (%s, {', '.join(['%s'] * len(cols))}, %s) "
            f"ON CONFLICT (show_date) DO UPDATE SET {sets}, raw=EXCLUDED.raw, parsed_at=now()",
            [sd, *[vals[c] for c in cols], Json(raw)])
        if qm.get("updated"):
            by_q = {q.get("q"): q for q in qm.get("quarters") or []}
            known_at = em[2] if em else dt.datetime.now(dt.timezone.utc)
            for p in qm.get("path") or []:
                if not p.get("estimate"):
                    continue                     # actuals are realized, not stated
                q = by_q.get(p["q"], {})
                period = p["q"]
                cur.execute(
                    "INSERT INTO hedgeye_quad_stated (note_date, product, scope, period, quad, "
                    "gdp_est, cpi_est, source_uid, snippet, known_at) "
                    "VALUES (%s, %s, 'quarterly', %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (note_date, product, scope, period, quad) DO UPDATE SET "
                    "gdp_est=EXCLUDED.gdp_est, cpi_est=EXCLUDED.cpi_est, parsed_at=now()",
                    (qm["updated"], PRODUCT, period, p["quad"], q.get("gdp_yoy"),
                     q.get("cpi_yoy"), f"tms_deck:{sd}",
                     f"TMS deck GIP model (Updated {qm['updated']}): {period} Quad {p['quad']}"
                     f"{' shallow' if p.get('shallow') else ''}", known_at))
                n_stated += 1
        conn.commit()
    return n_stated


def ingest(d: dict, show_date: str) -> dict:
    if d.get("show_date") != show_date:
        return {"stored": False, "errors": [f"skeleton show_date {d.get('show_date')} != {show_date}"]}
    hard, warn = validate(d)
    if hard:
        log.warning("tms: NOT stored — %s", hard)
        return {"stored": False, "errors": hard, "warnings": warn}
    n = upsert(d)
    return {"stored": True, "show_date": show_date, "stated_quad_rows": n, "warnings": warn}


# ─────────────────────────── link / show ───────────────────────────

def slides_link(day: dt.date) -> str | None:
    """The email's "TODAY'S SLIDES" target, resolved as far as
    app.hedgeye.com/mu/ — the S3 hop needs the logged-in browser."""
    row = _email_row(day)
    if not row:
        return None
    import httpx
    from bs4 import BeautifulSoup
    for a in BeautifulSoup(row[1] or "", "html.parser").find_all("a"):
        if not re.search(r"TODAY'?S SLIDES", a.get_text(" ", strip=True), re.I):
            continue
        url = htmlmod.unescape(a.get("href", ""))
        for _ in range(6):                       # follow tracking hops by hand
            if "app.hedgeye.com/mu/" in url:
                return url
            r = httpx.head(url, follow_redirects=False, timeout=15)
            nxt = r.headers.get("location")
            if not nxt:
                break
            url = nxt
    return None


def _fmt(v, suf=""):
    return "—" if v is None else f"{v:g}{suf}" if _num(v) else str(v)


def build_block(day: dt.date, full: bool = False) -> str:
    """Summary of the deck for `day`. full=True (the EOD pack) adds CFTC
    extremes, factor leaders, the cheapest IVol premiums and callout summaries."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT data_date, page_count, sections, top3, quad_model, sector_perf, "
                    "macro_vol, ivol, callouts, series, raw, cftc, style_factors "
                    "FROM tms_decks WHERE show_date=%s", (day,))
        row = cur.fetchone()
        cur.execute("SELECT quad_model_updated, quad_model FROM tms_decks "
                    "WHERE show_date < %s AND quad_model_updated IS NOT NULL "
                    "ORDER BY show_date DESC LIMIT 1", (day,))
        prev = cur.fetchone()
    if not row:
        return f"🎞 Macro Show deck {day}: not stored"
    data_date, pages, sections, top3, qm, sp, mv, iv, co, se, raw, cftc, sf = row
    out = [f"🎞 Macro Show deck {day} (tables = {data_date} close, {pages}p, "
           f"{len(sections.get('callouts', []))} callouts)"]
    if top3:
        out.append("Top 3: " + " · ".join(t.get("topic") or "?" for t in top3))
    if qm:
        path = " → ".join(f"{p['q']}{'E' if p.get('estimate') else ''} Q{p['quad']}"
                          f"{'s' if p.get('shallow') else ''}" for p in qm.get("path") or [])
        changed = prev and str(prev[0]) != qm.get("updated")
        out.append(f"Quad model (Updated {qm.get('updated')}{' — NEW' if changed else ''}): {path}")
        if changed:
            out.append("  ↳ model revised since the last deck: confirm the quarterly quad with QUAD:")
        cur_q = f"{(day.month - 1) // 3 + 1}Q{day.year % 100:02d}"
        deck_q = next((p["quad"] for p in qm.get("path") or [] if p["q"] == cur_q), None)
        try:
            from tools.quad_regime import current_quad_regime
            confirmed = current_quad_regime().get("quarterly_quad")
        except Exception as e:                   # read path down: say so, don't guess
            confirmed = f"unavailable ({e.__class__.__name__})"
        if deck_q:
            agree = confirmed == f"Quad {deck_q}"
            out.append(f"  ↳ {cur_q}: deck says Quad {deck_q}, confirmed quarterly is {confirmed}"
                       f"{'' if agree else ' ≠ (re-confirm with QUAD: if Hedgeye changed it)'}")
    if sp:
        spx = next((r for r in sp if r.get("ticker") == "SPX"), {})
        best = max((r for r in sp if r.get("ticker") != "SPX"), key=lambda r: r.get("d1") or -99)
        worst = min((r for r in sp if r.get("ticker") != "SPX"), key=lambda r: r.get("d1") or 99)
        out.append(f"SPX {_fmt(spx.get('price'))} {_fmt(spx.get('d1'), '%')} 1d, "
                   f"{_fmt(spx.get('ytd'), '%')} YTD · best {best['ticker']} {_fmt(best.get('d1'), '%')}"
                   f" · worst {worst['ticker']} {_fmt(worst.get('d1'), '%')}")
    if mv:
        out.append("Vol: " + " · ".join(f"{r['index']} {_fmt(r.get('last'))}" for r in mv))
    if se:
        pick = [r for r in se if re.search(r"MOVE|2yr|10yr Y|Spread|Momentum", r.get("series") or "", re.I)]
        if pick:
            out.append("Series: " + " · ".join(f"{r['series']} {_fmt(r.get('last'))}" for r in pick))
    if iv:
        hot = sorted((r for r in iv if _num(r.get("ivol_prem"))), key=lambda r: -r["ivol_prem"])[:3]
        out.append("Richest IVol premium: " + ", ".join(f"{r['ticker']} {r['ivol_prem']:g}%" for r in hot))
        if full:
            cold = sorted((r for r in iv if _num(r.get("ivol_prem"))), key=lambda r: r["ivol_prem"])[:3]
            out.append("Cheapest IVol premium: " + ", ".join(f"{r['ticker']} {r['ivol_prem']:g}%" for r in cold))
    if full and cftc:
        ext = sorted((r for r in cftc if _num(r.get("z_1y")) and abs(r["z_1y"]) >= 2),
                     key=lambda r: -abs(r["z_1y"]))
        if ext:
            out.append("CFTC extremes (1Y z): " + ", ".join(f"{r['metric']} {r['z_1y']:+g}" for r in ext))
    if full and sf:
        ranked = sorted((r for r in sf if _num(r.get("m1"))), key=lambda r: -r["m1"])
        if ranked:
            out.append(f"Factors 1M: best {ranked[0]['factor']} {ranked[0]['m1']:+g}% · "
                       f"worst {ranked[-1]['factor']} {ranked[-1]['m1']:+g}%")
    if co:
        if full:
            out.append("Callouts:")
            out.extend(f"  p{c.get('page')} {c.get('title') or '?'}: {c.get('summary') or ''}" for c in co)
        else:
            out.append("Callouts: " + "; ".join(c.get("title") or "?" for c in co))
    if (raw or {}).get("approx_fields"):
        # no _ or [ ]: Telegram Markdown reads them as italics/links and 400s
        out.append("approx: " + ", ".join(
            a.replace("_", " ").replace("[", "#").replace("]", "") for a in raw["approx_fields"]))
    return "\n".join(out)


_ID_COLS = ("ticker", "sector", "name", "factor", "metric", "index", "series", "label",
            "group", "section")


def _cell(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return f"{v:g}" if isinstance(v, float) else str(v)


def _table(rows: list[dict]) -> list[str]:
    """Every row, every column — identifiers first, then the deck's own order."""
    if not rows:
        return ["  (none)"]
    cols = []
    for r in rows:
        cols.extend(k for k in r if k not in cols)
    cols = [c for c in _ID_COLS if c in cols] + [c for c in cols if c not in _ID_COLS]
    grid = [cols] + [[_cell(r.get(c)) for c in cols] for r in rows]
    w = [min(max(len(g[i]) for g in grid), 40) for i in range(len(cols))]
    return ["  " + "  ".join(g[i][:40].ljust(w[i]) for i in range(len(cols))).rstrip()
            for g in grid]


def full_text(day: dt.date) -> str | None:
    """The WHOLE stored deck as plain text — every section, every row, the
    Top 3 and process pages verbatim, every callout with its levels. This is
    what goes to Telegram as one .txt file (REPORT UPLOAD pattern) so the
    desk LLM gets the full deck, not the summary card."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT row_to_json(t) FROM tms_decks t WHERE show_date=%s", (day,))
        row = cur.fetchone()
    if not row:
        return None
    r = row[0]
    out = [f"HEDGEYE MACRO SHOW DECK — show {r['show_date']} · tables = {r['data_date']} close · "
           f"{r['page_count']} pages", "", "SUMMARY", build_block(day, full=True), ""]
    out.append("=== TOP 3 (verbatim) ===")
    for t in r.get("top3") or []:
        out += [f"[p{t.get('page')}] {t.get('topic') or ''}".rstrip(), t.get("text") or "", ""]
    qm = r.get("quad_model") or {}
    if qm:
        out.append(f"=== GIP QUAD MODEL (updated {qm.get('updated')}) ===")
        out.append("  path: " + " → ".join(
            f"{p['q']} Q{p['quad']}{'s' if p.get('shallow') else ''}{' (est)' if p.get('estimate') else ''}"
            for p in qm.get("path") or []))
        q = qm.get("quarters")
        out += (_table(q) if isinstance(q, list) else [f"  {_cell(q)}"]) + [""]
    for p, v in sorted((r.get("process_text") or {}).items(), key=lambda kv: int(kv[0])):
        out += [f"=== OUR PROCESS p{p} ===", (v or {}).get("text", ""), ""]
    for key, title in (("sector_perf", "SECTOR PERFORMANCE"), ("sector_rel", "SECTOR RELATIVE TO SPX"),
                       ("volume", "VOLUME"), ("style_factors", "STYLE FACTORS"),
                       ("usd_corr", "$USD CORRELATIONS"), ("cftc", "CFTC POSITIONING"),
                       ("series", "SERIES (yields, spreads, …)"), ("macro_vol", "MACRO VOL"),
                       ("ivol", "IMPLIED vs REALIZED VOL")):
        out += [f"=== {title} ==="] + _table(r.get(key) or []) + [""]
    out.append("=== CHART CALLOUTS ===")
    for c in r.get("callouts") or []:
        out.append(f"[p{c.get('page')}] {c.get('title') or '?'}"
                   + (f"  (source: {c['source']})" if c.get("source") else ""))
        if c.get("summary"):
            out.append(f"  {c['summary']}")
        lv = c.get("levels") or {}
        rows = lv.get("rows") if isinstance(lv, dict) else lv
        if rows:
            out += _table(rows)
        rest = {k: v for k, v in c.items() if k not in ("page", "title", "source", "summary", "levels")}
        if rest:
            out.append("  " + _cell(rest))
        out.append("")
    if r.get("extra_pages"):
        out += ["=== OTHER PAGES ===", _cell(r["extra_pages"]), ""]
    out.append("Sections: " + ", ".join(f"{k} p{v[0]}-{v[-1]}" for k, v in (r.get("sections") or {}).items() if v))
    return "\n".join(out)


def eod_block(day: dt.date) -> list[str]:
    """MACRO SHOW DECK block for the EOD stat pack: the latest deck on or
    before `day`, labeled with the close its tables use (always the session
    BEFORE the show), so the pack never silently mixes sessions."""
    import db_pg
    if isinstance(day, str):
        day = dt.date.fromisoformat(day[:10])
    elif isinstance(day, dt.datetime) or hasattr(day, "to_pydatetime"):
        day = day.date()                         # datetime / pandas Timestamp
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(show_date) FROM tms_decks WHERE show_date <= %s", (day,))
        latest = cur.fetchone()[0]
    if not latest:
        return ["MACRO SHOW DECK: none captured yet"]
    lines = build_block(latest, full=True).split("\n")
    head = f"MACRO SHOW DECK — {latest}" + ("" if latest == day else
                                          f"   << NO DECK FOR {day}, showing the last one")
    return [head] + [l.replace("🎞 ", "") for l in lines]


def load_telegram_env() -> None:
    """Load TELEGRAM_BOT_TOKEN/CHAT_ID into this process from Railway when
    the laptop .env lacks them. Values are never printed or logged."""
    import os
    import shutil
    import subprocess
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        return
    exe = shutil.which("railway")
    if not exe:
        raise RuntimeError("no Telegram env and no railway CLI")
    out = subprocess.run([exe, "variables", "--kv"], capture_output=True, text=True,
                         timeout=60, cwd=str(pathlib.Path(__file__).resolve().parent))
    for line in out.stdout.splitlines():
        k, _, v = line.partition("=")
        if k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") and v:
            os.environ[k] = v.strip()
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        raise RuntimeError("railway variables had no Telegram settings")


def send_card(day: dt.date) -> bool:
    """The morning card: the WHOLE deck as one .txt attachment (Kris 9/26: the
    summary message was far too small; send it like REPORT UPLOAD). Caption =
    quad alert first when the model is new or disagrees with the confirmed quad."""
    text = full_text(day)
    if not text:
        return False
    lines = build_block(day).split("\n")
    alert = [l for l in lines if "NEW" in l or "≠" in l]
    caption = "\n".join(["⚠️ " + a.strip() for a in alert] + [lines[0]] + lines[1:2])
    load_telegram_env()
    from notifier import send_telegram_document
    return send_telegram_document(f"macro_show_deck_{day}.txt", text, caption)


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--link", action="store_true")
    g.add_argument("--prep", metavar="PDF")
    g.add_argument("--zoom", nargs=2, metavar=("PDF", "PAGE"))
    g.add_argument("--json", metavar="FILE")
    g.add_argument("--show", action="store_true")
    g.add_argument("--send", action="store_true", help="send the whole deck to Telegram as one .txt")
    g.add_argument("--full", action="store_true", help="print the whole-deck text that --send ships")
    g.add_argument("--eod", action="store_true", help="print the EOD-pack block")
    ap.add_argument("--date", help="show date YYYY-MM-DD (default: today ET)")
    ap.add_argument("--dry-run", action="store_true", help="validate only, no write")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import db_pg
    db_pg._load_dotenv_fallback()
    from zoneinfo import ZoneInfo
    day = (dt.date.fromisoformat(args.date) if args.date
           else dt.datetime.now(ZoneInfo("America/New_York")).date())

    if args.link:
        link = slides_link(day)
        print(link or f"NO Macro Show slide-deck email for {day}")
        return 0 if link else 1
    if args.show:
        print(build_block(day))
        return 0
    if args.eod:
        print("\n".join(eod_block(day)))
        return 0
    if args.full:
        text = full_text(day)
        print(text or f"no deck stored for {day}")
        return 0 if text else 1
    if args.send:
        ok = send_card(day)
        print("sent" if ok else f"NOT sent (deck for {day} stored? Telegram reachable?)")
        return 0 if ok else 1
    if args.zoom:
        import pymupdf
        pdf, n = pathlib.Path(args.zoom[0]), int(args.zoom[1])
        out = pdf.parent / f"tms_p{n:02d}_zoom.png"
        out.write_bytes(pymupdf.open(pdf)[n - 1].get_pixmap(
            matrix=pymupdf.Matrix(ZOOM_HI, ZOOM_HI)).tobytes("png"))
        print(out)
        return 0
    if args.prep:
        path = prep(args.prep, day.isoformat())
        skel = json.loads(path.read_text(encoding="utf-8"))
        print(f"skeleton: {path}")
        print("sections: " + ", ".join(f"{k} {v[0]}-{v[-1]}" for k, v in skel["sections"].items()
                                       if k != "divider"))
        for p in skel["_pages"]:
            print(f"  read p{p['page']:02d} -> {p['key']:13s} {p['title'][:70]}")
        return 0

    d = json.loads(open(args.json, encoding="utf-8").read())
    if args.dry_run:
        hard, warn = validate(d)
        print(json.dumps({"errors": hard, "warnings": warn}, indent=1))
        return 1 if hard else 0
    r = ingest(d, day.isoformat())
    print(json.dumps(r, indent=1, default=str))
    return 0 if r["stored"] else 1


if __name__ == "__main__":
    sys.exit(_cli())
