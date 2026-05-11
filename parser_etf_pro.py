"""ETF Pro / ETF Pro Plus email parser.

Hedgeye sends:
  - "ETF Pro Plus - New Weekly Report" (Monday weekly snapshot)
  - "UPDATE: N New ETF Pro Change(s)" (mid-week deltas)

Both contain a table with rows:
  AssetName TICKER DATE_ADDED RECENT_PRICE TREND_LOW TREND_HIGH ASSET_CLASS

Two sections (BULLISH / BEARISH) — bullish are LONG ideas, bearish are SHORT.

The parser:
  1. Extracts each ticker + range + bias
  2. Upserts hedgeye_etf_pro_ranges (PK: ticker, week_of)
  3. Notes the tickers in hedgeye_ticker_inventory + history (source='etf_pro')
  4. Calls unified_refresh.refresh_all_for_tickers so MFR/SG/Yahoo update in lockstep
  5. Stamps hedgeye_emails_raw.classified_as='etf_pro' + parsed_at

Idempotent: re-parsing the same email overwrites the same week's rows.

CLI:
  py parser_etf_pro.py --message-id <id>          # parse one specific email
  py parser_etf_pro.py --latest-weekly            # parse the most recent weekly
  py parser_etf_pro.py --backfill                 # parse all unparsed ETF Pro emails
  py parser_etf_pro.py --probe <message_id>       # parse but don't write (preview)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)


# ─────────────────────────── Subject detection ───────────────────────────

WEEKLY_SUBJECT_RE = re.compile(r"ETF Pro Plus.*Weekly", re.I)
UPDATE_SUBJECT_RE = re.compile(r"UPDATE.*\d+\s+New ETF Pro Change", re.I)
ANY_ETF_PRO_RE   = re.compile(r"\bETF\s*Pro\b", re.I)


def is_etf_pro_subject(subject: str) -> str:
    """Returns 'weekly' / 'update' / '' depending on classification."""
    if not subject:
        return ""
    if WEEKLY_SUBJECT_RE.search(subject):
        return "weekly"
    if UPDATE_SUBJECT_RE.search(subject):
        return "update"
    if ANY_ETF_PRO_RE.search(subject):
        return "other"
    return ""


# ─────────────────────────── Body parsing ───────────────────────────

# Row pattern: TICKER DATE_ADDED $RECENT_PRICE $TREND_LOW $TREND_HIGH ASSET_CLASS
# We anchor on TICKER (1-6 uppercase letters/digits, no spaces), then date in M/D/YYYY,
# then three dollar amounts. Asset class is whatever runs to end-of-line / next ticker.
ROW_RE = re.compile(
    r"\b([A-Z]{1,6})\s+"             # ticker
    r"(\d{1,2}/\d{1,2}/\d{4})\s+"    # date added
    r"\$([\d,]+\.\d{2})\s+"          # recent price
    r"\$([\d,]+\.\d{2})\s+"          # trend low
    r"\$([\d,]+\.\d{2})"             # trend high
)


def _strip_html_to_text(html_body: str) -> str:
    """Pull visible text out of the email HTML. Falls back to regex strip if
    BeautifulSoup isn't available."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_body, "html.parser")
        for tag in soup(["style", "script"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", text)
    except ImportError:
        text = re.sub(r"<style[^>]*>.*?</style>", "", html_body, flags=re.S | re.I)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


def parse_body(html_body: str) -> list[dict]:
    """Extract ticker rows from an ETF Pro email HTML body. Returns list of
    dicts {ticker, date_added, recent_price, range_low, range_high, bias}.
    Bias is 'long' (BULLISH section) or 'short' (BEARISH section)."""
    if not html_body:
        return []
    text = _strip_html_to_text(html_body)

    # Find BULLISH / BEARISH section boundaries in the text
    bull_start = text.upper().find("BULLISH")
    bear_start = text.upper().find("BEARISH")

    rows: list[dict] = []
    for m in ROW_RE.finditer(text):
        ticker = m.group(1)
        date_added = m.group(2)
        try:
            d_added = datetime.strptime(date_added, "%m/%d/%Y").date()
        except ValueError:
            continue
        try:
            recent = float(m.group(3).replace(",", ""))
            lo     = float(m.group(4).replace(",", ""))
            hi     = float(m.group(5).replace(",", ""))
        except ValueError:
            continue
        # Sanity: low < high, reject swapped numbers
        if lo > hi:
            lo, hi = hi, lo
        # Sanity: recent price is plausibly inside or near the range (within ±50% buffer)
        if recent < lo * 0.5 or recent > hi * 1.5:
            log.debug("rejecting row for %s: prices %s/%s/%s seem off", ticker, recent, lo, hi)
            continue
        # Decide bias by position in text
        pos = m.start()
        if bear_start > 0 and pos >= bear_start:
            bias = "short"
        else:
            bias = "long"
        rows.append({
            "ticker":       ticker,
            "date_added":   d_added,
            "recent_price": recent,
            "range_low":    lo,
            "range_high":   hi,
            "bias":         bias,
        })
    return rows


# ─────────────────────────── Week-of helper ───────────────────────────

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ─────────────────────────── Persistence ───────────────────────────

def upsert_rows(rows: list[dict], week_of: date, source_email_id: str | None) -> dict:
    """Write rows to hedgeye_etf_pro_ranges (ON CONFLICT DO UPDATE).
    Returns counts."""
    import db_pg
    written = 0
    failed = 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    desc = f"{r['bias'].upper()} | added {r['date_added']} | recent ${r['recent_price']}"
                    cur.execute(
                        """
                        INSERT INTO hedgeye_etf_pro_ranges
                            (ticker, week_of, range_low, range_high,
                             description, source_email_id, parsed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (ticker, week_of) DO UPDATE
                            SET range_low       = EXCLUDED.range_low,
                                range_high      = EXCLUDED.range_high,
                                description     = EXCLUDED.description,
                                source_email_id = EXCLUDED.source_email_id,
                                parsed_at       = NOW()
                        """,
                        (
                            r["ticker"], week_of,
                            r["range_low"], r["range_high"],
                            desc, source_email_id,
                        ),
                    )
                    written += 1
                except Exception as e:
                    log.warning("etf_pro upsert failed for %s: %s", r["ticker"], e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def note_in_inventory(rows: list[dict], message_id: str | None) -> int:
    """Note each ticker in hedgeye_ticker_inventory + history."""
    try:
        import ticker_inventory
    except Exception as e:
        log.warning("ticker_inventory unavailable: %s", e)
        return 0
    noted = 0
    for r in rows:
        try:
            ticker_inventory.note_tickers(
                [r["ticker"]],
                source="etf_pro",
                position="Long" if r["bias"] == "long" else "Short",
                signal_email_id=message_id,
                price_at_mention=r["recent_price"],
            )
            noted += 1
        except Exception as e:
            log.warning("note_tickers failed for %s: %s", r["ticker"], e)
    return noted


def stamp_email_parsed(message_id: str) -> None:
    """Mark the email as classified + parsed."""
    import db_pg
    try:
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE hedgeye_emails_raw "
                    "SET classified_as='etf_pro', parsed_at=NOW() "
                    "WHERE message_id=%s",
                    (message_id,),
                )
            conn.commit()
    except Exception as e:
        log.warning("stamp_email_parsed failed: %s", e)


# ─────────────────────────── Lockstep refresh ───────────────────────────

def fan_out_refresh(tickers: list[str]) -> dict:
    """Trigger MFR + SpotGamma + Yahoo refresh per the north-star architecture."""
    try:
        import unified_refresh
        return unified_refresh.refresh_all_for_tickers(
            tickers,
            capture_type="etf_pro",
            spotgamma_reason="etf_pro_weekly",
        )
    except Exception as e:
        log.warning("unified_refresh.refresh_all_for_tickers failed: %s", e)
        return {"error": str(e)}


# ─────────────────────────── High-level entry points ───────────────────────────

def process_email(message_id: str, *, dry_run: bool = False,
                  fan_out: bool = True) -> dict:
    """Parse one ETF Pro email by message_id. Returns summary.

    fan_out=False skips the heavy MFR/SG/Yahoo refresh call — set this when
    running backfill across many emails to keep the bridge subprocess within
    the 300s timeout. Live email processing should default to True.
    """
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT subject, received_at, html_body FROM hedgeye_emails_raw "
                "WHERE message_id=%s",
                (message_id,),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "email not found", "message_id": message_id}
    subject, received_at, html_body = row
    kind = is_etf_pro_subject(subject)
    if not kind:
        return {"error": "subject does not look like ETF Pro", "subject": subject}

    rows = parse_body(html_body)
    week_of = _monday_of(received_at.date()) if received_at else date.today()

    summary = {
        "message_id":    message_id,
        "subject":       subject,
        "received_at":   str(received_at),
        "kind":          kind,
        "week_of":       str(week_of),
        "rows_parsed":   len(rows),
        "tickers":       sorted({r["ticker"] for r in rows}),
        "dry_run":       dry_run,
    }
    if dry_run:
        summary["sample_rows"] = rows[:5]
        return summary

    upsert = upsert_rows(rows, week_of, source_email_id=message_id)
    summary["upsert"] = upsert
    summary["noted_in_inventory"] = note_in_inventory(rows, message_id)
    stamp_email_parsed(message_id)
    if rows and fan_out:
        summary["fan_out"] = fan_out_refresh([r["ticker"] for r in rows])
    elif rows:
        summary["fan_out"] = "skipped (fan_out=False)"
    return summary


def process_latest_weekly(dry_run: bool = False) -> dict:
    """Find and process the most recent 'ETF Pro Plus - New Weekly Report'."""
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "ORDER BY received_at DESC LIMIT 1",
                ("%ETF Pro Plus%Weekly%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no ETF Pro Plus weekly email found"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    """Walk every ETF Pro email and parse it if classified_as is not already
    'etf_pro'. Returns summary.

    fan_out defaults to False during backfill: writing 15+ emails × 39 tickers
    × 3 sources of refresh API calls is too slow for one bridge subprocess
    (300s timeout). Run unified_refresh separately afterward, OR run
    --fanout-latest to refresh just the most recent week's tickers.
    """
    import db_pg
    summary = {
        "processed": 0, "skipped": 0, "errors": [],
        "tickers_touched": set(),
    }
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id, subject FROM hedgeye_emails_raw "
                "WHERE (subject ILIKE %s OR subject ILIKE %s) "
                "  AND COALESCE(classified_as,'') <> 'etf_pro' "
                "ORDER BY received_at ASC",
                ("%ETF Pro Plus%", "%ETF Pro Change%"),
            )
            candidates = cur.fetchall()
    for message_id, subject in candidates:
        kind = is_etf_pro_subject(subject)
        if not kind:
            summary["skipped"] += 1
            continue
        try:
            result = process_email(message_id, fan_out=fan_out)
            summary["processed"] += 1
            for t in result.get("tickers", []):
                summary["tickers_touched"].add(t)
        except Exception as e:
            summary["errors"].append({"message_id": message_id, "error": str(e)})
    summary["tickers_touched"] = sorted(summary["tickers_touched"])
    return summary


def fanout_latest_weekly_tickers() -> dict:
    """Refresh MFR/SG/Yahoo for the tickers in the most recent ETF Pro weekly.

    Run this after `--backfill` to populate downstream sources without
    timing out the bridge. ~39 tickers, all sources, single dedupe.
    """
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT week_of, MAX(parsed_at) "
                "FROM hedgeye_etf_pro_ranges "
                "GROUP BY week_of ORDER BY week_of DESC LIMIT 1"
            )
            latest = cur.fetchone()
            if not latest:
                return {"error": "no etf_pro rows yet — run --backfill first"}
            week_of = latest[0]
            cur.execute(
                "SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges "
                "WHERE week_of = %s ORDER BY ticker",
                (week_of,),
            )
            tickers = [r[0] for r in cur.fetchall()]
    return {
        "week_of": str(week_of),
        "tickers": tickers,
        "fan_out": fan_out_refresh(tickers),
    }


# ─────────────────────────── CLI ───────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(description="ETF Pro email parser")
    ap.add_argument("--message-id", help="Parse one specific email by message_id")
    ap.add_argument("--latest-weekly", action="store_true",
                    help="Parse the most recent ETF Pro Plus weekly email")
    ap.add_argument("--backfill", action="store_true",
                    help="Parse every unparsed ETF Pro email (default: skip fan-out)")
    ap.add_argument("--backfill-with-fanout", action="store_true",
                    help="Backfill AND fan out per email (slow, may time out)")
    ap.add_argument("--fanout-latest", action="store_true",
                    help="Run MFR/SG/Yahoo refresh for the most recent weekly's tickers")
    ap.add_argument("--probe", help="Parse one email by message_id but don't write")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if args.probe:
        result = process_email(args.probe, dry_run=True)
    elif args.message_id:
        result = process_email(args.message_id)
    elif args.latest_weekly:
        result = process_latest_weekly()
    elif args.backfill_with_fanout:
        result = backfill_all_unparsed(fan_out=True)
    elif args.backfill:
        result = backfill_all_unparsed(fan_out=False)
    elif args.fanout_latest:
        result = fanout_latest_weekly_tickers()
    else:
        ap.print_help(); return 1
    print(json.dumps(result, indent=2, default=str), flush=True)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
