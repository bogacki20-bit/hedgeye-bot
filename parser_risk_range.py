"""
Risk Range Signals Parser.

Reads unparsed emails from hedgeye_emails_raw, identifies Risk Range Signals
emails by subject, extracts:
  1. Per-ticker Risk Range rows → hedgeye_risk_ranges
  2. TREND CHANGE blocks → hedgeye_signal_changes (change_type='trend_change')
  3. #OUTBUCKET removals → hedgeye_signal_changes (change_type='out_bucket')

Sets classified_as = 'risk_range' (or 'risk_range_parse_failed') and parsed_at
on the raw email row when done. Idempotent — safe to re-run; existing
hedgeye_risk_ranges PKs are updated in place.

Email format (verified against 2026-05-05 fixture):
  Header section: "RISK RANGE™ SIGNALS", date stamp, "How to use ...".
  TREND CHANGE section: "TICKER changed from OLD to NEW" lines.
  Main table: rows of
      TICKER (TREND)
      Description<whitespace>buy_trade<whitespace>sell_trade<whitespace>prev_close
  #OUTBUCKET section: "Description (TICKER) = TREND (date)" lines.

Tickers in this email range from 3 chars (SPX, RUT, VIX) to 7 chars
(BITCOIN, EUR/USD), can include digits (UST30Y) or slashes (USD/YEN).

Architecture:
  email_parser.py drops raw emails into the lake (hedgeye_emails_raw).
  This module reads from the lake and populates the typed Risk Range tables.
  They run as separate loops; the parser doesn't block email ingestion.

Operational:
  Run via main.py as a thread, or standalone for one-off testing:
      python parser_risk_range.py
  For testing against a fixture file (no DB):
      python parser_risk_range.py --fixture fixtures/risk_range_2026-05-05.txt
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, date
from typing import Optional

from bs4 import BeautifulSoup

# db_pg is imported lazily inside DB-touching functions so that the regex
# smoke test (which has no DB) can run in environments without psycopg2.

log = logging.getLogger(__name__)

PARSER_INTERVAL    = int(os.getenv("PARSER_INTERVAL", "900"))     # 15 min
PARSER_BATCH_SIZE  = int(os.getenv("PARSER_BATCH_SIZE", "100"))


# ─────────────────────────── Email type detection ───────────────────────────

RISK_RANGE_SUBJECT_PATTERNS = [
    re.compile(r"risk\s*range", re.IGNORECASE),
    re.compile(r"\bRRS\b"),
]


def is_risk_range_email(subject: str | None) -> bool:
    """True if email subject looks like a Risk Range Signals email."""
    if not subject:
        return False
    return any(p.search(subject) for p in RISK_RANGE_SUBJECT_PATTERNS)


# ─────────────────────────── Patterns ───────────────────────────

# Ticker tokens: uppercase letters/digits with optional . or / or - inside.
# Covers: SPX, BRK.B, EUR/USD, UST30Y, BITCOIN.
# Length 1-9 to be safe — Hedgeye uses up to 7 chars (BITCOIN, EUR/USD).
TICKER_TOKEN = r"[A-Z][A-Z0-9]{0,2}(?:[A-Z0-9./\-][A-Z0-9]{0,4})?"

# A single number cell: digits with optional commas, optional decimal w/ 0-4 places.
# Examples: "4.93", "7,075", "212.00", "0.730", "76,294", "169".
NUMBER_RAW = r"\$?\s*[\d,]+(?:\.\d{1,4})?"

# Block format on extracted text:
#   TICKER (TREND)
#   Description text<space/tab>num1<space/tab>num2<space/tab>num3
TICKER_BLOCK_RE = re.compile(
    r"^(?P<ticker>" + TICKER_TOKEN + r")"
    r"\s+\((?P<trend>BULLISH|BEARISH|NEUTRAL)\)\s*\r?\n"
    r"(?P<desc>[^\r\n]+?)"
    r"[ \t]+(?P<low>"  + NUMBER_RAW + r")"
    r"[ \t]+(?P<high>" + NUMBER_RAW + r")"
    r"[ \t]+(?P<prev>" + NUMBER_RAW + r")"
    r"\s*$",
    re.MULTILINE,
)

# TREND CHANGE: "ORCL changed from Neutral to Bullish"
TREND_CHANGE_RE = re.compile(
    r"^(?P<ticker>" + TICKER_TOKEN + r")"
    r"\s+changed\s+from\s+(?P<prev>BULLISH|BEARISH|NEUTRAL)"
    r"\s+to\s+(?P<new>BULLISH|BEARISH|NEUTRAL)\b",
    re.IGNORECASE | re.MULTILINE,
)

# OUTBUCKET line: "Aerospace & Defense ETF (ITA) = Bearish (8/22/2024)"
OUTBUCKET_RE = re.compile(
    r"^(?P<desc>[^\n]+?)"
    r"\s*\((?P<ticker>" + TICKER_TOKEN + r")\)"
    r"\s*=\s*(?P<trend>BULLISH|BEARISH|NEUTRAL)"
    r"\s*\((?P<date>\d{1,2}/\d{1,2}/\d{4})\)",
    re.IGNORECASE | re.MULTILINE,
)


# ─────────────────────────── Body extraction ───────────────────────────

def extract_text_from_html(html: str) -> str:
    """
    Convert HTML body to plain text suitable for our regex patterns.

    We use separator='\\n' so each table cell ends up on its own line. That
    lines up with the TICKER_BLOCK_RE pattern which expects:
        TICKER (TREND)        <-- one line
        Description    NUM NUM NUM   <-- next line, tab/space separated

    Hedgeye's HTML email format places ticker+trend in one cell (separated
    by <br>) and the three numbers in three more cells, so a row's cells
    end up as 4 lines: ticker-line, description-line, num-num-num... wait,
    actually that's not quite right. We rely on the regex pattern matching
    the actual extracted output, which we'll verify empirically.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def extract_via_html_tables(html: str, message_id: str,
                            signal_date: date) -> list[dict]:
    """
    Walk HTML <table> elements directly. More robust than text regex if the
    email is structured as a real table.

    For each <tr>: collect cell text, look for "TICKER (TREND)" pattern in
    any cell, then expect 3 numeric cells. The numeric cells may be the
    last 3 of the row.
    """
    if not html:
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")

    cell_re = re.compile(
        r"\b(" + TICKER_TOKEN + r")\s*\((BULLISH|BEARISH|NEUTRAL)\)",
        re.IGNORECASE,
    )

    for tr in soup.find_all("tr"):
        cells = [td.get_text(separator=" ", strip=True)
                 for td in tr.find_all(["td", "th"])]
        if len(cells) < 4:
            continue

        # Find ticker+trend in any of the early cells
        ticker = trend = description = None
        for i, cell in enumerate(cells[:2]):
            m = cell_re.search(cell)
            if not m:
                continue
            ticker = m.group(1).upper()
            trend = m.group(2).upper()
            # Description: whatever's after the ticker/trend, plus next cell
            tail = cell[m.end():].strip()
            description = tail or (cells[i + 1] if i + 1 < len(cells) else None)
            break

        if not ticker:
            continue
        if ticker in seen:
            continue

        # Last 3 cells should be numbers
        nums = []
        for cell in cells[-3:]:
            cleaned = cell.replace(",", "").replace("$", "").strip()
            try:
                nums.append(float(cleaned))
            except ValueError:
                nums = None
                break
        if not nums or len(nums) != 3:
            continue

        buy_trade, sell_trade, prev_close = nums
        if not _valid_range(buy_trade, sell_trade):
            continue

        seen.add(ticker)
        rows.append({
            "ticker":          ticker,
            "signal_date":     signal_date,
            "trend":           trend,
            "buy_trade":       buy_trade,
            "sell_trade":      sell_trade,
            "prev_close":      prev_close,
            "description":     description,
            "source_email_id": message_id,
        })

    return rows


def extract_via_text(text: str, message_id: str,
                     signal_date: date) -> list[dict]:
    """Regex-based fallback against extracted plain text."""
    rows: list[dict] = []
    seen: set[str] = set()

    for m in TICKER_BLOCK_RE.finditer(text):
        d = m.groupdict()
        ticker = d["ticker"].upper()
        if ticker in seen:
            continue
        try:
            buy_trade  = float(d["low"].replace(",", ""))
            sell_trade = float(d["high"].replace(",", ""))
            prev_close = float(d["prev"].replace(",", ""))
        except (ValueError, AttributeError):
            log.warning(f"    [{ticker}] non-numeric range, skipping")
            continue
        if not _valid_range(buy_trade, sell_trade):
            log.warning(f"    [{ticker}] invalid range "
                        f"{buy_trade}-{sell_trade}, skipping")
            continue
        seen.add(ticker)
        rows.append({
            "ticker":          ticker,
            "signal_date":     signal_date,
            "trend":           d["trend"].upper(),
            "buy_trade":       buy_trade,
            "sell_trade":      sell_trade,
            "prev_close":      prev_close,
            "description":     (d["desc"] or "").strip(),
            "source_email_id": message_id,
        })

    return rows


def extract_trend_changes(text: str, message_id: str,
                          signal_date: date) -> list[dict]:
    """Extract TREND CHANGE entries: 'TICKER changed from OLD to NEW'."""
    rows: list[dict] = []
    for m in TREND_CHANGE_RE.finditer(text):
        rows.append({
            "ticker":          m.group("ticker").upper(),
            "change_type":     "trend_change",
            "prev_state":      m.group("prev").upper(),
            "new_state":       m.group("new").upper(),
            "signal_date":     signal_date,
            "source_email_id": message_id,
        })
    return rows


def extract_outbucket(text: str, message_id: str,
                      _signal_date: date) -> list[dict]:
    """
    Extract #OUTBUCKET entries.

    Each entry has its own removal date in parens — that becomes the
    signal_date for the change row (NOT the email's date), so the change
    history is preserved correctly even across re-parses.
    """
    rows: list[dict] = []
    for m in OUTBUCKET_RE.finditer(text):
        try:
            removal_date = datetime.strptime(m.group("date"), "%m/%d/%Y").date()
        except ValueError:
            continue
        rows.append({
            "ticker":          m.group("ticker").upper(),
            "change_type":     "out_bucket",
            "prev_state":      None,
            "new_state":       m.group("trend").upper(),
            "signal_date":     removal_date,
            "source_email_id": message_id,
        })
    return rows


def _valid_range(low: float, high: float) -> bool:
    return low > 0 and high > 0 and low < high


# ─────────────────────────── Per-email parser ───────────────────────────

def parse_risk_range_email(raw_row: dict) -> tuple[list[dict], list[dict]]:
    """
    Extract risk-range rows + signal-change rows from one raw email row.

    Returns (range_rows, signal_change_rows). signal_change_rows contains
    BOTH trend_change AND out_bucket entries; both go into the same table.
    """
    message_id  = raw_row["message_id"]
    body_html   = raw_row.get("html_body") or ""
    body_text   = raw_row.get("text_body") or ""
    received_at = raw_row.get("received_at")

    if isinstance(received_at, datetime):
        signal_date = received_at.date()
    elif isinstance(received_at, date):
        signal_date = received_at
    else:
        signal_date = date.today()

    # Try HTML table walk first — most robust if email is a real <table>.
    range_rows = extract_via_html_tables(body_html, message_id, signal_date)
    if not range_rows:
        # Fallback: regex against extracted/plain text
        text = extract_text_from_html(body_html) if body_html else body_text
        if text and len(text.strip()) >= 200:
            range_rows = extract_via_text(text, message_id, signal_date)
        else:
            log.warning(f"  [{message_id}] body too short for fallback parser")

    # Trend changes + outbucket: regex over flattened text either way
    text_for_changes = extract_text_from_html(body_html) if body_html else body_text
    change_rows = extract_trend_changes(text_for_changes, message_id, signal_date)
    change_rows.extend(extract_outbucket(text_for_changes, message_id, signal_date))

    return range_rows, change_rows


# ─────────────────────────── Cycle / loop ───────────────────────────

def run_parser_cycle(batch_size: int = PARSER_BATCH_SIZE) -> dict:
    """
    Process up to `batch_size` unparsed Risk Range emails. Returns a summary dict.

    Filters at the DB level on subject pattern so the cycle is not blocked by
    a backlog of unparsed non-Risk-Range emails (other typed parsers handle
    those). Failed parses are classified as 'risk_range_parse_failed' so they
    don't get retried.
    """
    import db_pg  # lazy — only needed when running against the live lake
    import psycopg2.extras

    summary = {
        "emails_examined":           0,
        "emails_parsed":              0,
        "emails_skipped":             0,
        "emails_failed":              0,
        "range_rows_written":         0,
        "signal_change_rows_written": 0,
    }

    # Pull only emails whose subject already looks like Risk Range. Anything
    # else stays in the raw lake for other typed parsers to claim.
    with db_pg.get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM hedgeye_emails_raw
                WHERE parsed_at IS NULL
                  AND (subject ILIKE %s OR subject ILIKE %s OR subject ~* %s)
                ORDER BY received_at ASC
                LIMIT %s
                """,
                ('%RISK  RANGE%', '%RISK RANGE%', r'\yRRS\y', batch_size),
            )
            candidates = [dict(r) for r in cur.fetchall()]

    summary["emails_examined"] = len(candidates)

    for raw_row in candidates:
        message_id = raw_row["message_id"]
        subject    = raw_row.get("subject") or ""

        if not is_risk_range_email(subject):
            # Defensive: SQL prefilter should make this rare. Mark so it
            # doesn't reappear next cycle.
            db_pg.mark_email_classified(message_id, "risk_range_subject_mismatch")
            summary["emails_skipped"] += 1
            continue

        try:
            range_rows, change_rows = parse_risk_range_email(raw_row)
        except Exception as e:
            log.error(f"  [{message_id}] parse error: {e}", exc_info=True)
            db_pg.mark_email_classified(message_id, "risk_range_parse_failed")
            summary["emails_failed"] += 1
            continue

        if not range_rows and not change_rows:
            log.warning(f"  [{message_id}] subject matched but no rows extracted "
                        f"— marking parse_failed for inspection")
            db_pg.mark_email_classified(message_id, "risk_range_parse_failed")
            summary["emails_failed"] += 1
            continue

        if range_rows:
            summary["range_rows_written"] += db_pg.save_risk_range_rows(range_rows)
        if change_rows:
            summary["signal_change_rows_written"] += db_pg.save_signal_changes(change_rows)

        # Note each ticker in the inventory (running record of every ticker
        # Hedgeye has signaled on, with current Quad regime tagged). Quad
        # regime comes from project memory + most recent Macro Themes update;
        # for now hardcoded to "Quad 2" until we wire a quad lookup.
        try:
            import ticker_inventory
            from db_pg import get_conn

            # Collect tickers + map trend -> position where derivable. Range
            # rows carry a `trend` field (bullish/bearish/neutral) which maps
            # cleanly to inventory's position taxonomy. Signal-change rows
            # don't (trend_change has from/to states, out_bucket is a removal),
            # so they're added without a position arg.
            tickers_in_email: set[str] = set()
            ticker_to_position: dict[str, str] = {}
            for r in range_rows or []:
                t = r.get("ticker")
                if not t:
                    continue
                tickers_in_email.add(t)
                trend = (r.get("trend") or "").strip().lower()
                if trend in ("bullish", "up", "long"):
                    ticker_to_position[t] = "long"
                elif trend in ("bearish", "down", "short"):
                    ticker_to_position[t] = "short"
            for r in change_rows or []:
                if r.get("ticker"):
                    tickers_in_email.add(r["ticker"])

            # Pull current Quad regime from the corpus rather than hardcoding.
            # monitor_context.get_hedgeye_ctx returns {} on any error so we
            # gracefully fall back to None (which note_tickers tolerates).
            try:
                import monitor_context
                quad = monitor_context.get_hedgeye_ctx().get("current_quad")
            except Exception:
                quad = None

            if tickers_in_email:
                # First pass: bulk note_tickers without position so every
                # ticker gets at least an inventory + history row.
                ticker_inventory.note_tickers(
                    tickers_in_email,
                    source=ticker_inventory.SOURCE_RISK_RANGE,
                    quad_regime=quad,
                    message_id=message_id,
                )
                # Second pass: per-ticker re-note with the trend-derived
                # position so the history row has it. This mirrors what
                # email_parser does for classifier-derived positions.
                for sym, pos in ticker_to_position.items():
                    ticker_inventory.note_ticker(
                        sym,
                        source=ticker_inventory.SOURCE_RISK_RANGE,
                        position=pos,
                        quad_regime=quad,
                        message_id=message_id,
                    )
                log.info(
                    f"  [{message_id}] ticker_inventory: {len(tickers_in_email)} ticker(s) "
                    f"({len(ticker_to_position)} with position), quad={quad}"
                )

                # Lockstep refresh of MFR + SpotGamma + Yahoo via the unified
                # orchestrator. Every Risk Range mention triggers all three
                # tools to update together. Each leg independently wrapped.
                try:
                    import unified_refresh
                    refresh_summary = unified_refresh.refresh_all_for_tickers(
                        list(tickers_in_email),
                        capture_type="email_arrival",
                        spotgamma_reason="risk_range",
                    )
                    log.info(f"  [{message_id}] " +
                             unified_refresh._format_summary_log(refresh_summary))
                except Exception as e:
                    log.warning(f"  [{message_id}] unified_refresh failed: {e}")
        except Exception as e:
            log.warning(f"  [{message_id}] ticker inventory hook failed: {e}")

        db_pg.mark_email_classified(message_id, "risk_range")
        summary["emails_parsed"] += 1

        log.info(
            f"  [{message_id}] parsed | ranges={len(range_rows)} "
            f"changes={len(change_rows)} subject={subject[:60]!r}"
        )

    return summary


def process_one(message_id: str, *, fan_out: bool = True) -> dict:
    """Parse a single email by message_id. Mirrors parser_etf_pro.process_email
    so email_parser can dispatch RR inline the moment the email lands, instead
    of waiting up to PARSER_INTERVAL for the daemon loop.

    Returns {kind, rows_parsed, signal_changes_parsed, ticker_count} or
    {error} on hard failure. `fan_out=False` skips unified_refresh on the
    assumption that email_parser already ran it for this email.
    """
    import db_pg
    import psycopg2.extras

    out = {"kind": "risk_range", "rows_parsed": 0,
           "signal_changes_parsed": 0, "ticker_count": 0}

    with db_pg.get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM hedgeye_emails_raw WHERE message_id = %s",
                (message_id,),
            )
            row = cur.fetchone()
    if not row:
        return {"error": f"message_id {message_id!r} not found"}
    raw_row = dict(row)
    subject = raw_row.get("subject") or ""
    if not is_risk_range_email(subject):
        return {"error": f"subject does not match RR pattern: {subject[:80]!r}"}

    try:
        range_rows, change_rows = parse_risk_range_email(raw_row)
    except Exception as e:
        log.error(f"  [{message_id}] parse error: {e}", exc_info=True)
        db_pg.mark_email_classified(message_id, "risk_range_parse_failed")
        return {"error": f"parse_risk_range_email raised: {e!s}"}

    if not range_rows and not change_rows:
        db_pg.mark_email_classified(message_id, "risk_range_parse_failed")
        return {"error": "subject matched but no rows extracted"}

    if range_rows:
        out["rows_parsed"] = db_pg.save_risk_range_rows(range_rows)
    if change_rows:
        out["signal_changes_parsed"] = db_pg.save_signal_changes(change_rows)

    # Inventory + (optional) lockstep refresh — mirrors run_parser_cycle's tail.
    try:
        import ticker_inventory
        tickers_in_email: set[str] = set()
        ticker_to_position: dict[str, str] = {}
        for r in range_rows or []:
            t = r.get("ticker")
            if not t:
                continue
            tickers_in_email.add(t)
            trend = (r.get("trend") or "").strip().lower()
            if trend in ("bullish", "up", "long"):
                ticker_to_position[t] = "long"
            elif trend in ("bearish", "down", "short"):
                ticker_to_position[t] = "short"
        for r in change_rows or []:
            if r.get("ticker"):
                tickers_in_email.add(r["ticker"])
        try:
            import monitor_context
            quad = monitor_context.get_hedgeye_ctx().get("current_quad")
        except Exception:
            quad = None
        if tickers_in_email:
            ticker_inventory.note_tickers(
                tickers_in_email,
                source=ticker_inventory.SOURCE_RISK_RANGE,
                quad_regime=quad,
                message_id=message_id,
            )
            for sym, pos in ticker_to_position.items():
                ticker_inventory.note_ticker(
                    sym,
                    source=ticker_inventory.SOURCE_RISK_RANGE,
                    position=pos,
                    quad_regime=quad,
                    message_id=message_id,
                )
            out["ticker_count"] = len(tickers_in_email)
            if fan_out:
                try:
                    import unified_refresh
                    unified_refresh.refresh_all_for_tickers(
                        list(tickers_in_email),
                        capture_type="email_arrival",
                        spotgamma_reason="risk_range",
                    )
                except Exception as e:
                    log.warning(f"  [{message_id}] unified_refresh failed: {e}")
    except Exception as e:
        log.warning(f"  [{message_id}] ticker inventory hook failed: {e}")

    db_pg.mark_email_classified(message_id, "risk_range")
    log.info(
        f"  [{message_id}] process_one parsed | ranges={out['rows_parsed']} "
        f"changes={out['signal_changes_parsed']} tickers={out['ticker_count']}"
    )
    return out


def run_parser_loop(interval: int = PARSER_INTERVAL):
    """Forever loop. Runs a parser cycle every `interval` seconds."""
    log.info(f"Starting Risk Range parser loop — interval={interval}s, "
             f"batch={PARSER_BATCH_SIZE}")
    while True:
        try:
            summary = run_parser_cycle()
            if summary["emails_examined"] > 0:
                log.info(f"Parser cycle done: {summary}")
        except Exception as e:
            log.error(f"Parser cycle error: {e}", exc_info=True)
        time.sleep(interval)


# ─────────────────────────── Standalone test ───────────────────────────

def _smoke_test_fixture(path: str) -> int:
    """
    Run all three extractors against a text fixture file. NO database access.
    Use for offline regex verification.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    fake_message_id = f"fixture::{os.path.basename(path)}"
    fake_signal_date = date.today()

    ranges  = extract_via_text(text, fake_message_id, fake_signal_date)
    changes = extract_trend_changes(text, fake_message_id, fake_signal_date)
    outbck  = extract_outbucket(text, fake_message_id, fake_signal_date)

    print(f"--- Risk Range rows ({len(ranges)}) ---")
    for r in ranges:
        print(f"  {r['ticker']:8} {r['trend']:8} "
              f"buy={r['buy_trade']:>10}  sell={r['sell_trade']:>10}  "
              f"prev={r['prev_close']:>10}  "
              f"desc={(r['description'] or '')[:40]!r}")

    print(f"\n--- TREND CHANGE rows ({len(changes)}) ---")
    for r in changes:
        print(f"  {r['ticker']:8} {r['prev_state']} → {r['new_state']}")

    print(f"\n--- OUTBUCKET rows ({len(outbck)}) ---")
    for r in outbck:
        print(f"  {r['ticker']:8} {r['new_state']:8} (removed {r['signal_date']})")

    print(f"\nTotals: {len(ranges)} ranges, {len(changes)} trend changes, "
          f"{len(outbck)} outbucket")
    return 0 if (ranges or changes or outbck) else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", help="Run regex smoke test against a text fixture file")
    args = parser.parse_args()

    if args.fixture:
        sys.exit(_smoke_test_fixture(args.fixture))

    print("Running one Risk Range parser cycle against the live lake...")
    summary = run_parser_cycle()
    print(f"Summary: {summary}")
