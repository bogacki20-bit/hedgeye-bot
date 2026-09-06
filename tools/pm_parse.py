"""Position Monitor PDF parser — the bucket-maintainer bucket_history was
built for. Parses the pypdf text of a 'HEDGEYE POSITION MONITOR (MM/DD/YYYY)'
long/short-list PDF into {ticker: bucket} + sector context, then (via
ingest_hook, wired into doc_ingest) diffs it against ticker_tags buckets with
tools.bucket_history.sync_buckets — every transition stamped with the note
date and the frozen monthly+quarterly quad.

Layout (operator PDF, 7/6/26 reference): sector headers and the six bucket
headers are ALL-CAPS lines (leading space unreliable); each ticker is a
single all-caps token on its own line followed by 1+ company-name lines.
Name lines can themselves be ALL CAPS ('SMITHFIELD FOODS INC') or
ticker-shaped ('AVAXUSD' names itself) — the first line after a ticker is
always its name. Tickers are stored EXACTLY as written (BTCUSD, 2513.HK,
VOLV-B.ST — never translated).

QUAD_RELIABLE_SINCE: operator-confirmed 2026-07-12 — quad stamps on rows
effective before 2026-07-06 must not be trusted by backtests.
"""
from __future__ import annotations

import logging
import re
from datetime import date

log = logging.getLogger(__name__)

# Quad state is operator-seeded and only reliable from this date forward.
QUAD_RELIABLE_SINCE = date(2026, 7, 6)

BUCKET_HEADERS = {
    "ACTIVE LONGS":    "active_long",
    "ACTIVE SHORTS":   "active_short",
    "TOP IDEA LONGS":  "top_idea_long",
    "TOP IDEA SHORTS": "top_idea_short",
    "LONG BENCH":      "long_bench",
    "SHORT BENCH":     "short_bench",
}

_MASTHEAD_RE = re.compile(
    r"HEDGEYE\s+POSITION\s+MONITOR\s*\((\d{1,2})/(\d{1,2})/(20\d{2})\)", re.I)
# Single token: AAPL · BF-B · RI.PA · 2513.HK · 005930.KS · VOLV-B.ST · BTCUSD
# Must contain a LETTER — the OCR path (Claude vision transcription) emits
# pure-numeric fragments ('2513', '100.00') that are ticker-shaped but never
# tickers. Same rule sync_position_monitor.py applies to the CSV feed; the
# OCR feed had no equivalent until 2026-08-23.
_TICKER_RE = re.compile(r"^(?=.*[A-Z])[A-Z0-9]{1,7}(?:[.\-\^][A-Z0-9]{1,4}){0,2}$")
# If a sector's roster ever shrinks the mapping below this fraction of the
# stored roster, removal detection is refused (partial upload ≠ mass exit).
REMOVAL_GUARD_FRACTION = 0.6

# The Position Monitor's OWN 15 sectors — the canonical vocabulary, stored
# UPPERCASE in ticker_tags.hedgeye_group. parse_position_monitor Title-cases
# headers for readability ("Consumer Staples", "Global Tech") while GLL stays
# an acronym, so every write and every comparison goes through pm_sector_key().
#
# This set is a GUARD, not documentation: sync_buckets refuses the whole ingest
# if a parsed sector is not in it. Hedgeye adding a 16th sector, or a header
# mis-read, must stop the load loudly — silently creating a 16th group would
# mint a bucket no sector cap covers and nobody would see it appear.
PM_SECTORS = frozenset({
    "RESTAURANTS", "CONSUMER STAPLES", "CANNABIS", "GLL", "RETAIL",
    "HEALTHCARE", "FINANCIALS", "DIGITAL ASSETS", "SMALL CAPS", "INDUSTRIALS",
    "MATERIALS", "ENERGY", "SOFTWARE", "COMMUNICATIONS", "GLOBAL TECH",
})


def pm_sector_key(sector) -> str | None:
    """Canonical storage form of a PM sector name, or None if blank.
    Whitespace-collapsed and uppercased. Membership in PM_SECTORS is NOT
    checked here — validation belongs to the caller so it can refuse loudly."""
    s = re.sub(r"\s+", " ", str(sector or "").strip()).upper()
    return s or None


def _is_caps_header(line: str) -> bool:
    """ALL-CAPS line containing a letter — sector or bucket header shape."""
    s = line.strip()
    return bool(s) and s == s.upper() and bool(re.search(r"[A-Z]", s))


def parse_position_monitor(text: str) -> dict:
    """pypdf text -> {'report_date': date|None,
                      'mapping': {ticker: bucket},
                      'sectors': {ticker: sector},
                      'names':   {ticker: company name},
                      'warnings': [str, ...]}
    Pure — no DB, no I/O. Unparseable lines are warned, never dropped
    silently."""
    out = {"report_date": None, "mapping": {}, "sectors": {}, "names": {},
           "warnings": []}
    m = _MASTHEAD_RE.search(text or "")
    if m:
        mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            out["report_date"] = date(yy, mm, dd)
        except ValueError:
            out["warnings"].append(f"masthead date invalid: {m.group(0)}")
    else:
        out["warnings"].append("no masthead date found — UNDATED")

    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]      # keep indices lookahead-safe
    sector, bucket = None, None
    pending = None          # ticker awaiting its first name line

    for i, s in enumerate(lines):
        if _MASTHEAD_RE.search(s):
            continue
        hdr = BUCKET_HEADERS.get(s.upper())
        if hdr:
            if pending:
                out["warnings"].append(
                    f"{pending}: bucket header before any name line")
                pending = None
            bucket = hdr
            continue
        if pending:
            # First line after a ticker is ALWAYS its company name — even
            # when caps ('SMITHFIELD FOODS INC') or ticker-shaped (AVAXUSD).
            out["names"][pending] = s
            pending = None
            continue
        # A ticker-shaped line directly followed by a bucket header is a
        # SECTOR that fits the ticker pattern (GLL, RETAIL, ENERGY on the
        # 7/6 PDF) — a real ticker always has its name line first.
        nxt = lines[i + 1].upper() if i + 1 < len(lines) else ""
        if _TICKER_RE.match(s) and nxt not in BUCKET_HEADERS:
            t = s
            if bucket is None:
                out["warnings"].append(f"{t}: ticker before any bucket header")
                continue
            if t in out["mapping"] and out["mapping"][t] != bucket:
                out["warnings"].append(
                    f"{t}: listed twice ({out['mapping'][t]} then {bucket}) "
                    f"— keeping LAST")
            out["mapping"][t] = bucket
            out["sectors"][t] = sector
            pending = t
            continue
        if _is_caps_header(s):
            # Title-case for readability; short single tokens (GLL) are
            # acronyms and stay as written.
            sector = s.title() if (" " in s or len(s) > 4) else s
            bucket = None       # a new sector resets the bucket context
            continue
        # Anything else is a name continuation ('Stock' wrap line) — only
        # meaningful directly after a name; otherwise it's noise. Attach to
        # the most recent ticker if it has a name already.
        if out["names"]:
            last = next(reversed(out["names"]))
            out["names"][last] += " " + s
        else:
            out["warnings"].append(f"unparsed line: {s!r}")

    if pending:
        out["warnings"].append(f"{pending}: file ended before its name line")
    if not out["mapping"]:
        out["warnings"].append("no tickers parsed — layout change?")
    return out


def diff_summary(transitions: list, roster_size: int, report_date,
                 removals_skipped: bool = False) -> str:
    """Telegram-ready CHANGES block from sync_buckets transition dicts."""
    hdr = (f"PM {report_date or 'UNDATED ⚠'} · roster {roster_size} · "
           f"{len(transitions)} change{'s' if len(transitions) != 1 else ''}")
    if removals_skipped:
        hdr += " · ⚠ removals NOT checked (partial upload guard)"
    if not transitions:
        return hdr + "\n  no bucket changes vs stored roster"
    adds  = [t for t in transitions if t["from"] is None
             and t["to"] != "removed"]
    drops = [t for t in transitions if t["to"] == "removed"]
    moves = [t for t in transitions if t["from"] is not None
             and t["to"] != "removed"]
    parts = [hdr]
    if adds:
        parts.append("  NEW: " + " ".join(
            f"{t['ticker']}({t['to']})" for t in adds))
    if moves:
        parts.append("  MOVED: " + " ".join(
            f"{t['ticker']} {t['from']}→{t['to']}" for t in moves))
    if drops:
        parts.append("  REMOVED: " + " ".join(t["ticker"] for t in drops))
    return "\n".join(parts)


def ingest_hook(row_id, note_date, text) -> str:
    """doc_ingest deep-parse entry: parse, sync buckets (dated with the
    REPORT date, not today), return the CHANGES block for the upload reply.
    Removal detection only on full uploads (guard fraction)."""
    import db_pg
    from tools.bucket_history import sync_buckets, UnknownPMSector

    p = parse_position_monitor(text)
    eff = p["report_date"] or note_date
    if eff is None:
        return ("⚠ PM parse: UNDATED (no masthead, no note date) — NOT "
                "synced. A fact without a date isn't a fact.")
    if not p["mapping"]:
        return "⚠ PM parse: 0 tickers — NOT synced. " + \
            " · ".join(p["warnings"][:3])

    # Write gate before ticker_tags sync: the mapping comes from vision
    # transcription of a screenshot, so ticker-SHAPED word tokens (MORRIS,
    # WIDEST) and near-misses (BUXXX) survive the shape regex. Membership or
    # a live quote decides; drops are surfaced in the reply, not silent.
    try:
        from tools.symbol_guard import validate_for_storage
        kept, dropped = validate_for_storage(list(p["mapping"]), "pm_parse")
        if dropped:
            p["warnings"].append(
                "symbol guard dropped %d unresolvable token(s): %s"
                % (len(dropped), " ".join(str(d) for d in dropped)))
            p["mapping"] = {t: b for t, b in p["mapping"].items() if t in kept}
            p["sectors"] = {t: s for t, s in p["sectors"].items() if t in kept}
        if not p["mapping"]:
            return "⚠ PM parse: every token failed symbol validation — " \
                   "NOT synced. " + " · ".join(p["warnings"][:3])
    except Exception as e:
        log.warning("symbol_guard unavailable, PM mapping unvalidated: %s", e)

    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ticker_tags "
                    "WHERE hedgeye_bucket_0629 IS NOT NULL")
        stored = cur.fetchone()[0]
    detect = stored == 0 or \
        len(p["mapping"]) >= REMOVAL_GUARD_FRACTION * stored
    # p["sectors"] rides along with the buckets now. Before 2026-08-16 it was
    # parsed and thrown away, so the PM's own sector never reached the database
    # and ticker_tags.hedgeye_group sat frozen at its one-time seed.
    try:
        res = sync_buckets(p["mapping"], source_email_id=f"doc:{row_id}",
                           detect_removals=detect, effective_date=eff,
                           sectors=p["sectors"])
    except UnknownPMSector as e:
        return "🛑 PM sync REFUSED — %s" % e
    reply = diff_summary(res["detail"], len(p["mapping"]), eff,
                         removals_skipped=not detect)
    if p["warnings"]:
        reply += "\n  ⚠ " + " · ".join(p["warnings"][:5])
    return reply
