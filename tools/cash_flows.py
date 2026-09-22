"""cash_flows.py — ingest NON-TRADE cash rows from Fidelity's
History_for_Account export (operator 9/22: the 'missing $3K' was
spending — the Individual account doubles as checking, and the trade
importer skips those rows by design, so ~$4K/wk was invisible).

ingest(path)     parses History_for_Account_*.csv, stores deposits /
                 withdrawals / debit cards / checks / margin interest /
                 dividends into cash_flows (row_hash dedupe, idempotent).
                 Trades, journals and short-vs-margin MTM pairs (net-0
                 internal) are skipped.
week_summary()   7-day flow line for the desk note:
                 'FLOWS 7d: spent $X (cards a/checks b) · deposits $Y ...'

_daily_upload runs ingest automatically when a History_for_Account file
sits in Downloads. The file only exists when Kris downloads it, so the
freshness contract is loose (9d).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import logging
import pathlib

log = logging.getLogger(__name__)

_SKIP = ("MARK TO MARKET", "JOURNAL", "REINVESTMENT as of")


def _kind(action: str) -> str | None:
    u = action.upper()
    if u.startswith("YOU "):
        return None                       # trades — book_activity's job
    if any(s in u for s in _SKIP):
        return None                       # internal net-zero pairs
    if "DEBIT CARD" in u:
        return "debit_card"
    if "CHECK PAID" in u:
        return "check"
    if "TRANSFER" in u or "EFT" in u:
        return "deposit"                  # signed amount says direction
    if "MARGIN INTEREST" in u:
        return "margin_interest"
    if "DIVIDEND" in u or u.startswith("DIV ") or "INTEREST EARNED" in u:
        return "dividend"
    if "FEE" in u:
        return "fee"
    return "other"


def ingest(path: str | pathlib.Path) -> dict:
    import db_pg
    path = pathlib.Path(path)
    # account number from the filename (History_for_Account_X96383748...)
    import re
    m = re.search(r"Account_([A-Z0-9]+)", path.name)
    acct = m.group(1) if m else "unknown"
    rows, skipped = [], 0
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if len(r) < 16 or not r[0].strip() or "/" not in r[0]:
                continue
            try:
                d = dt.datetime.strptime(r[0].strip(), "%m/%d/%Y").date()
            except ValueError:
                continue
            action = r[1]
            kind = _kind(action)
            if kind is None:
                skipped += 1
                continue
            try:
                amt = float(r[14].replace(",", ""))
            except ValueError:
                continue
            if kind == "other" and abs(amt) < 0.5:
                continue
            pending = (r[15] or "").strip().lower() == "processing"
            h = hashlib.sha1(f"{acct}|{d}|{action}|{amt}".encode()).hexdigest()
            rows.append((acct, d, kind, amt, action[:180], pending, h))
    written = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute("""
                INSERT INTO cash_flows (account_number, flow_date, kind,
                                        amount, description, pending, row_hash)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (row_hash) DO UPDATE SET pending=EXCLUDED.pending
                """, row)
            written += cur.rowcount
        conn.commit()
    return {"file": path.name, "account": acct, "parsed": len(rows),
            "written": written, "non_flow_skipped": skipped}


def week_summary(days: int = 7) -> str | None:
    """One desk-note line, or None when no flow data covers the window."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT kind, sum(amount) FROM cash_flows
            WHERE flow_date >= CURRENT_DATE - %s
            GROUP BY kind""", (days,))
        agg = dict(cur.fetchall())
        cur.execute("SELECT max(flow_date) FROM cash_flows")
        latest = cur.fetchone()[0]
    if not agg:
        return None
    cards = float(agg.get("debit_card", 0))
    checks = float(agg.get("check", 0))
    dep = float(agg.get("deposit", 0))
    intr = float(agg.get("margin_interest", 0))
    div = float(agg.get("dividend", 0))
    spent = cards + checks + intr
    net = spent + dep + div
    stale = f" ⚠data thru {latest}" if latest and \
        (dt.date.today() - latest).days > 3 else ""
    return (f"FLOWS {days}d: spent ${-spent:,.0f} "
            f"(cards ${-cards:,.0f} · checks ${-checks:,.0f}"
            + (f" · margin int ${-intr:,.0f}" if intr < -0.5 else "")
            + f") · deposits ${dep:+,.0f} · net ${net:+,.0f}{stale}")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import db_pg
    db_pg._load_dotenv_fallback()
    if len(sys.argv) > 1:
        print(ingest(sys.argv[1]))
    print(week_summary())
