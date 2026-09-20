"""parser_pm_changes.py — Position Monitor CHANGE emails -> sector monitor
state (operator 9/20: a position monitor for EACH sector, not just the
Monday PDF).

Two Sunday products, both text-parseable:

  "Week of M/D/YYYY: Position Monitors Update"   (info@, all 15 sectors)
      Communications
      TTD : removing from Long Bench
      FUBO : moving to Top Idea Short from Active Short
      U : moving to Active Long from Top Idea Long
  "Weekly Position Monitor | N ... Changes"      (Financials Pro)
      Moved to Best Idea Long: FICO
      Moved to Long Bench: MA, MCO, SPGI, PYPL, TRU, FOUR

Every move names the ticker's NEW tier, so an append-only event log
carried forward equals the current monitor per sector — no OCR of the
image-only lists needed. current_monitor(sector) = latest event per
ticker, 'removed' rows excluded.

CLI: python parser_pm_changes.py --backfill | --latest | --probe <mid>
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import date

log = logging.getLogger(__name__)

UPDATE_RE = re.compile(r"Position\s+Monitors\s+Update", re.I)
FINPM_RE = re.compile(r"Weekly\s+Position\s+Monitor\s*\|", re.I)

# All-sector update grammar: "TKR : moving to X from Y" | "adding to X"
# | "removing from X"
_MOVE_RE = re.compile(
    r"\b(?P<tkr>[A-Z][A-Z0-9.]{0,5})\s*:\s*"
    r"(?P<verb>moving|adding|removing)\s+"
    r"(?:to|from)\s+(?P<tier>Top\s+Idea|Active|Long\s+Bench|Short\s+Bench|"
    r"Best\s+Idea)?\s*(?P<side>Long|Short|Bench)?", re.I)
# Financials PM grammar: "Moved to <Tier> <Side>: A, B, C"
_MOVED_RE = re.compile(
    r"Moved\s+to\s+(?P<tier>Best\s+Idea|Active|Long\s+Bench|Short\s+Bench)"
    r"\s*(?P<side>Long|Short)?\s*:\s*(?P<list>[A-Z0-9 ,.]*)", re.I)

# section headers in the all-sector email (subset that matters is fine —
# unknown headers still switch the sector label)
_SECTORS = ("Communications|Consumer Staples|Retail|Financials|Health ?Care|"
            "Healthcare|Industrials|Technology|Energy|Utilities|Materials|"
            "REITs|Real Estate|Gaming|GLL|Housing|Consumer Discretionary|"
            "Media|Internet|Cannabis|China|Software|Semis|Transports|Defense|"
            "Crypto(?:currency)?|Fintech|Digital Assets|Bitcoin|"
            "Consumer Finance|Restaurants|Leisure|Airlines|Autos|Telecom")
_SECTION_RE = re.compile(rf"\b(?P<sec>{_SECTORS})\b")


def _strip_html(html: str) -> str:
    t = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html or "",
               flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    import html as H
    return re.sub(r"\s+", " ", H.unescape(t)).strip()


def _norm_tier(tier: str | None, side: str | None, verb: str) -> tuple[str, str | None]:
    """(tier, side) normalized. 'removing' -> ('removed', side-if-known)."""
    t = re.sub(r"\s+", "_", (tier or "").strip().lower())
    s = (side or "").strip().lower() or None
    if t in ("long_bench", "short_bench"):
        s = t.split("_")[0]
        t = "bench"
    if s == "bench":                       # "moving to ... Bench" split oddly
        s = None
        t = t or "bench"
    if verb.lower() == "removing":
        return "removed", s
    return (t or "unknown"), s


def parse_update_email(body: str) -> list[dict]:
    """The all-sector 'Position Monitors Update': walk the text, tracking
    the current sector heading; each TKR: move emits one event."""
    rows = []
    # bound the region: changes run from 'Changes from' to the disclaimer
    m = re.search(r"Changes\s+from[^:]*:?(.*)$", body, re.I | re.S)
    seg = m.group(1) if m else body
    sector = "unknown"
    pos = 0
    tokens = []           # (index, kind, payload)
    for sm in _SECTION_RE.finditer(seg):
        tokens.append((sm.start(), "sec", sm.group("sec")))
    for mm in _MOVE_RE.finditer(seg):
        tokens.append((mm.start(), "move", mm))
    tokens.sort(key=lambda x: x[0])
    for _i, kind, payload in tokens:
        if kind == "sec":
            sector = re.sub(r"\s+", " ", payload).strip()
            continue
        mm = payload
        tier, side = _norm_tier(mm.group("tier"), mm.group("side"),
                                mm.group("verb"))
        rows.append({"sector": sector, "ticker": mm.group("tkr").upper(),
                     "tier": tier, "side": side,
                     "verb": mm.group("verb").lower(),
                     "raw": mm.group(0)[:120]})
    return rows


def parse_finpm_email(body: str) -> list[dict]:
    """Financials Pro 'Weekly Position Monitor': 'Moved to <Tier>: A, B'."""
    rows = []
    for mm in _MOVED_RE.finditer(body):
        tier, side = _norm_tier(mm.group("tier"), mm.group("side"), "moved")
        for tk in re.findall(r"\b[A-Z][A-Z0-9.]{0,5}\b", mm.group("list")):
            rows.append({"sector": "Financials", "ticker": tk, "tier": tier,
                         "side": side, "verb": "moved",
                         "raw": mm.group(0)[:120]})
    return rows


def process_email(message_id: str, dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT subject, received_at, html_body, text_body "
                    "FROM hedgeye_emails_raw WHERE message_id=%s",
                    (message_id,))
        r = cur.fetchone()
    if not r:
        return {"error": "email not found"}
    subject, received_at, html, text = r
    body = _strip_html(html) if html else (text or "")
    if UPDATE_RE.search(subject or ""):
        rows = parse_update_email(body)
    elif FINPM_RE.search(subject or ""):
        rows = parse_finpm_email(body)
    else:
        return {"error": "not a PM-changes subject", "subject": subject}
    # junk-gate the free-text scan the same way retail does
    from tools.symbol_guard import filter_rows
    rows, dropped = filter_rows(rows, "pm_changes")
    d = received_at.date() if received_at else date.today()
    out = {"message_id": message_id, "subject": (subject or "")[:70],
           "rows": len(rows), "dropped": len(dropped), "dry_run": dry_run}
    if dry_run or not rows:
        out["sample"] = rows[:8]
        return out
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute("""
                INSERT INTO hedgeye_sector_monitor_events
                    (signal_date, sector, ticker, tier, side, verb, raw,
                     source_email_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (signal_date, sector, ticker, source_email_id)
                DO UPDATE SET tier=EXCLUDED.tier, side=EXCLUDED.side,
                              verb=EXCLUDED.verb, parsed_at=NOW()""",
                        (d, row["sector"], row["ticker"], row["tier"],
                         row["side"], row["verb"], row["raw"], message_id))
        conn.commit()
    return out


def current_monitor(sector: str) -> list[tuple]:
    """[(ticker, tier, side, as_of)] — latest event per ticker in the
    sector, removed rows excluded. The carry-forward monitor."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (ticker) ticker, tier, side, signal_date
            FROM hedgeye_sector_monitor_events
            WHERE sector ILIKE %s
            ORDER BY ticker, signal_date DESC, parsed_at DESC""",
                    (sector,))
        rows = [r for r in cur.fetchall() if r[1] != "removed"]
    return rows


_SUBJ_SQL = ("(subject ILIKE '%%Position Monitors Update%%' "
             "OR subject ILIKE 'Weekly Position Monitor |%%')")


def backfill() -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT message_id, subject FROM hedgeye_emails_raw "
                    f"WHERE {_SUBJ_SQL} ORDER BY received_at")
        mids = cur.fetchall()
    ok = fail = 0
    for mid, subj in mids:
        r = process_email(mid)
        print(f"  {r.get('rows', 0):3d} rows  {subj[:60]!r}")
        if r.get("error"):
            fail += 1
        else:
            ok += 1
    return {"processed": ok, "failed": fail}


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    import json
    if "--backfill" in sys.argv:
        print(json.dumps(backfill(), indent=1))
    elif "--probe" in sys.argv:
        mid = sys.argv[sys.argv.index("--probe") + 1]
        print(json.dumps(process_email(mid, dry_run=True), indent=1,
                         default=str))
    else:
        print(__doc__)
