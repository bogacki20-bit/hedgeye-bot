"""Portfolio Solutions parser — Keith McCullough's daily ETF re-rank.

Hedgeye sends "Portfolio Solutions: Daily ETF Re-Rank (M/D/YYYY)" with:
  - Ordered list of macro ETFs by rank
  - Top/Bottom Movers (1w + 1m)
  - **Keith's Commentary**: literally what he traded that day
    e.g. "In the PA today, I sold 100bps of BUXX, OIH, and STIP. Sold 50bps EQRR, SOYB."

The commentary is the mimic-Keith signal — we extract it into
hedgeye_portfolio_actions with (action, ticker, bps).

Writes:
  - hedgeye_portfolio_solutions: full rank list (snapshot_date, rank, ticker)
  - hedgeye_portfolio_actions: parsed commentary actions (sold/bought + bps + ticker)

CLI:
  py parser_portfolio_solutions.py --message-id <id>
  py parser_portfolio_solutions.py --latest
  py parser_portfolio_solutions.py --backfill
  py parser_portfolio_solutions.py --probe <id>
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


SUBJECT_RE = re.compile(r"Portfolio\s+Solutions.*Daily\s+ETF\s+Re-?Rank", re.I)


def is_portfolio_solutions_subject(subject: str) -> bool:
    if not subject:
        return False
    return bool(SUBJECT_RE.search(subject))


# ─────────────────────────── Body parsing ───────────────────────────

def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["style", "script"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
        return re.sub(r"\n{3,}", "\n\n", text)
    except ImportError:
        text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()


# Extract the section: "Macro ETFs by Rank: FDRXX, BUXX, CLOX, ..."
RANK_BLOCK_RE = re.compile(
    r"Macro ETFs by Rank\s*:\s*(.*?)(?:Keith'?s Commentary|VIEW LARGER|Watch Keith)",
    re.I | re.S,
)
TICKER_TOKEN_RE = re.compile(r"\b([A-Z]{2,6})\b")

# Extract the commentary block (between "Keith's Commentary" header and the next section)
COMMENTARY_BLOCK_RE = re.compile(
    r"Keith'?s Commentary[:\s]*\"?([^\"]+?)\"?\s*(?:Watch Keith|VIEW LARGER|\Z)",
    re.I | re.S,
)

# Action sentences inside the commentary:
#   "I sold 100bps of BUXX, OIH, and STIP."
#   "Sold 50bps EQRR, SOYB."
#   "Added 100bps to OIH."
#   "Trimmed 50bps OIH."
ACTION_SENTENCE_RE = re.compile(
    r"\b(sold|bought|added|trimmed|covered)\s+(\d{1,3})\s*bps\s*"
    r"(?:of\s+|to\s+|in\s+)?"
    r"([A-Z][A-Z0-9,\s]+?)(?:\.|$|;)",
    re.I,
)
TICKER_LIST_RE = re.compile(r"\b([A-Z]{2,6})\b")


def parse_body(html_body: str, snapshot_date: date) -> dict:
    """Returns {ranks: [{rank, ticker}], actions: [{action, bps, ticker, commentary}]}."""
    text = _strip_html(html_body)
    result = {"ranks": [], "actions": []}

    # Ranks
    m = RANK_BLOCK_RE.search(text)
    if m:
        rank_block = m.group(1)
        seen = set()
        rank = 0
        for tm in TICKER_TOKEN_RE.finditer(rank_block):
            tk = tm.group(1)
            # filter common non-ticker tokens
            if tk in {"AND", "THE", "FOR", "USE", "PA", "PS"}:
                continue
            if tk in seen:
                continue
            seen.add(tk)
            rank += 1
            result["ranks"].append({"rank": rank, "ticker": tk})

    # Commentary -> actions
    cm = COMMENTARY_BLOCK_RE.search(text)
    if cm:
        commentary = cm.group(1).strip()
        for am in ACTION_SENTENCE_RE.finditer(commentary):
            action = am.group(1).lower()
            try:
                bps = int(am.group(2))
            except ValueError:
                continue
            ticker_str = am.group(3)
            for tm in TICKER_LIST_RE.finditer(ticker_str):
                tk = tm.group(1)
                if tk in {"AND", "THE", "FOR", "OF", "TO", "BPS", "IN", "PA"}:
                    continue
                # Skip junk like "PA" or context words
                if len(tk) < 2:
                    continue
                result["actions"].append({
                    "action":     action,
                    "bps":        bps,
                    "ticker":     tk,
                    "commentary": commentary[:500],
                })

    return result


# ─────────────────────────── Snapshot date from subject ───────────────────────────

SUBJECT_DATE_RE = re.compile(r"\((\d{1,2})/(\d{1,2})/(\d{4})\)")

def extract_snapshot_date(subject: str, fallback: date) -> date:
    m = SUBJECT_DATE_RE.search(subject or "")
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return fallback


# ─────────────────────────── Persistence ───────────────────────────

def upsert_ranks(ranks: list[dict], snapshot_date: date, message_id: str | None) -> dict:
    import db_pg
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for r in ranks:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_portfolio_solutions
                            (snapshot_date, rank, ticker, source_email_id, parsed_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON CONFLICT (snapshot_date, ticker) DO UPDATE
                            SET rank            = EXCLUDED.rank,
                                source_email_id = EXCLUDED.source_email_id,
                                parsed_at       = NOW()
                        """,
                        (snapshot_date, r["rank"], r["ticker"], message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("PS rank upsert failed for %s: %s", r["ticker"], e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def insert_actions(actions: list[dict], action_date: date, message_id: str | None) -> dict:
    import db_pg
    written, failed = 0, 0
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for a in actions:
                try:
                    cur.execute(
                        """
                        INSERT INTO hedgeye_portfolio_actions
                            (action_date, ticker, action, bps,
                             commentary_raw, source_email_id, captured_at)
                        VALUES (%s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (source_email_id, ticker, action) DO NOTHING
                        """,
                        (action_date, a["ticker"], a["action"], a["bps"],
                         a["commentary"], message_id),
                    )
                    written += 1
                except Exception as e:
                    log.warning("PS action insert failed for %s: %s", a["ticker"], e)
                    failed += 1
            conn.commit()
    return {"written": written, "failed": failed}


def stamp_email_parsed(message_id: str) -> None:
    import db_pg
    try:
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE hedgeye_emails_raw "
                    "SET classified_as='portfolio_solutions', parsed_at=NOW() "
                    "WHERE message_id=%s",
                    (message_id,),
                )
            conn.commit()
    except Exception as e:
        log.warning("stamp_email_parsed failed: %s", e)


def note_in_inventory(tickers: list[str], message_id: str | None) -> int:
    try:
        import ticker_inventory
        noted = ticker_inventory.note_tickers(
            tickers,
            source="portfolio_solutions",
            message_id=message_id,
        )
        return noted.get("noted", 0)
    except Exception as e:
        log.warning("note_tickers failed: %s", e)
        return 0


def fan_out_refresh(tickers: list[str]) -> dict:
    try:
        import unified_refresh
        return unified_refresh.refresh_all_for_tickers(
            tickers,
            capture_type="portfolio_solutions",
            spotgamma_reason="portfolio_solutions",
        )
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────── High-level entry points ───────────────────────────

def process_email(message_id: str, *, dry_run: bool = False,
                  fan_out: bool = True) -> dict:
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
    if not is_portfolio_solutions_subject(subject):
        return {"error": "subject not Portfolio Solutions", "subject": subject}

    snapshot_date = extract_snapshot_date(subject, received_at.date() if received_at else date.today())
    parsed = parse_body(html_body, snapshot_date)

    summary = {
        "message_id":   message_id,
        "subject":      subject,
        "snapshot_date": str(snapshot_date),
        "ranks_parsed":   len(parsed["ranks"]),
        "actions_parsed": len(parsed["actions"]),
        "dry_run":      dry_run,
    }
    if dry_run:
        summary["sample_ranks"]   = parsed["ranks"][:10]
        summary["sample_actions"] = parsed["actions"][:10]
        return summary

    if parsed["ranks"]:
        summary["ranks_upsert"] = upsert_ranks(
            parsed["ranks"], snapshot_date, message_id,
        )
    if parsed["actions"]:
        summary["actions_insert"] = insert_actions(
            parsed["actions"], snapshot_date, message_id,
        )

    tickers = sorted({r["ticker"] for r in parsed["ranks"]} |
                    {a["ticker"] for a in parsed["actions"]})
    summary["noted_in_inventory"] = note_in_inventory(tickers, message_id)
    stamp_email_parsed(message_id)
    if tickers and fan_out:
        summary["fan_out"] = fan_out_refresh(tickers)
    elif tickers:
        summary["fan_out"] = "skipped"
    return summary


def process_latest(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "ORDER BY received_at DESC LIMIT 1",
                ("%Portfolio Solutions%Daily ETF Re-Rank%",),
            )
            row = cur.fetchone()
    if not row:
        return {"error": "no Portfolio Solutions emails found"}
    return process_email(row[0], dry_run=dry_run)


def backfill_all_unparsed(fan_out: bool = False) -> dict:
    import db_pg
    summary = {"processed": 0, "errors": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id, subject FROM hedgeye_emails_raw "
                "WHERE subject ILIKE %s "
                "  AND COALESCE(classified_as,'') <> 'portfolio_solutions' "
                "ORDER BY received_at ASC",
                ("%Portfolio Solutions%Daily ETF Re-Rank%",),
            )
            candidates = cur.fetchall()
    for message_id, subject in candidates:
        try:
            process_email(message_id, fan_out=fan_out)
            summary["processed"] += 1
        except Exception as e:
            summary["errors"].append({"message_id": message_id, "error": str(e)})
    return summary


# ─────────────────────────── CLI ───────────────────────────

def _cli() -> int:
    ap = argparse.ArgumentParser(description="Portfolio Solutions parser")
    ap.add_argument("--message-id", help="Parse one specific email")
    ap.add_argument("--latest", action="store_true",
                    help="Parse the most recent PS email")
    ap.add_argument("--backfill", action="store_true",
                    help="Parse every unparsed PS email")
    ap.add_argument("--probe", help="Parse one email but don't write")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if args.probe:
        result = process_email(args.probe, dry_run=True)
    elif args.message_id:
        result = process_email(args.message_id)
    elif args.latest:
        result = process_latest()
    elif args.backfill:
        result = backfill_all_unparsed(fan_out=False)
    else:
        ap.print_help(); return 1
    print(json.dumps(result, indent=2, default=str), flush=True)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
