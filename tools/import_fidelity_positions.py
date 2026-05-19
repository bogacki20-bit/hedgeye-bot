"""Upsert a Fidelity Portfolio_Positions CSV into portfolio_positions (PG).

Schema (Fidelity export):
  Account Number, Account Name, Symbol, Description, Quantity, Last Price,
  Last Price Change, Current Value, Today's Gain/Loss Dollar,
  Today's Gain/Loss Percent, Total Gain/Loss Dollar, Total Gain/Loss Percent,
  Percent Of Account, Cost Basis Total, Average Cost Basis, Type

Cash rows: Symbol ends in ** OR symbol in {SPAXX, FDRXX, CORE} OR
description hints "MONEY MARKET". Skipped — we model them as account cash,
not investable positions.

Some tickers have two rows in one account (Cash + Margin). We keep both,
distinguished by the Type column (account_type in DB). Natural key:
(account_number, symbol, account_type, snapshot_date).
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("import_fidelity_positions")

CASH_SUFFIX = re.compile(r".+\*\*$")
CASH_SYMBOLS = {"SPAXX", "FDRXX", "CORE", "FZFXX"}


def _money(s: str | None) -> float | None:
    if s is None:
        return None
    t = str(s).strip().replace("$", "").replace(",", "").replace("+", "")
    if t in ("", "--", "n/a", "N/A"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _pct(s: str | None) -> float | None:
    if s is None:
        return None
    t = str(s).strip().replace("%", "").replace("+", "")
    if t in ("", "--"):
        return None
    try:
        return float(t) / 100.0
    except ValueError:
        return None


def _is_cash(row: dict) -> bool:
    sym = (row.get("Symbol") or "").strip().upper()
    if not sym:
        return True
    if CASH_SUFFIX.match(sym):
        return True
    if sym in CASH_SYMBOLS:
        return True
    desc = (row.get("Description") or "").upper()
    if "MONEY MARKET" in desc or "PENDING ACTIVITY" in desc:
        return True
    return False


def _snapshot_date_from_filename(path: Path) -> date:
    """Fidelity filenames carry the as-of date: Portfolio_Positions_May-11-2026.csv."""
    m = re.search(r"([A-Za-z]{3,9})-?(\d{1,2})[-_,]?\s*(\d{4})", path.stem)
    if m:
        try:
            from datetime import datetime
            mon, day, year = m.group(1), m.group(2), m.group(3)
            return datetime.strptime(f"{mon} {day} {year}", "%B %d %Y").date()
        except ValueError:
            try:
                from datetime import datetime
                return datetime.strptime(f"{mon} {day} {year}", "%b %d %Y").date()
            except ValueError:
                pass
    return date.today()


def ingest(csv_path: str | Path) -> dict:
    import db_pg
    from tools._snapshot_sync import sync as _snapshot_sync
    from tools.ticker_aliases import normalize_ticker

    p = Path(csv_path).expanduser().resolve()
    snap = _snapshot_date_from_filename(p)
    log.info("ingest: %s snapshot_date=%s", p, snap)

    summary = {"path": str(p), "snapshot_date": str(snap),
               "rows_seen": 0, "rows_cash_skipped": 0,
               "rows_upserted": 0, "rows_failed": 0,
               "errors": []}

    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                for row in reader:
                    summary["rows_seen"] += 1
                    acct = (row.get("Account Number") or "").strip()
                    if not acct or acct.startswith("Brokerage services"):
                        continue
                    if _is_cash(row):
                        summary["rows_cash_skipped"] += 1
                        continue
                    sym = (row.get("Symbol") or "").strip().upper()
                    if not sym:
                        summary["rows_cash_skipped"] += 1
                        continue
                    # Savepoint per row so one error doesn't poison the txn.
                    cur.execute("SAVEPOINT row_sp")
                    try:
                        cur.execute(
                            """
                            INSERT INTO portfolio_positions (
                                snapshot_date, account_number, account_name,
                                symbol, normalized_symbol, description,
                                quantity, last_price, current_value,
                                today_gl_dollar, today_gl_pct,
                                total_gl_dollar, total_gl_pct,
                                pct_of_account,
                                cost_basis, avg_cost_basis,
                                account_type, ingested_at
                            ) VALUES (
                                %s, %s, %s,
                                %s, %s, %s,
                                %s, %s, %s,
                                %s, %s,
                                %s, %s,
                                %s,
                                %s, %s,
                                %s, NOW()
                            )
                            ON CONFLICT (account_number, symbol,
                                         COALESCE(account_type, ''), snapshot_date)
                            DO UPDATE SET
                                quantity        = EXCLUDED.quantity,
                                last_price      = EXCLUDED.last_price,
                                current_value   = EXCLUDED.current_value,
                                today_gl_dollar = EXCLUDED.today_gl_dollar,
                                today_gl_pct    = EXCLUDED.today_gl_pct,
                                total_gl_dollar = EXCLUDED.total_gl_dollar,
                                total_gl_pct    = EXCLUDED.total_gl_pct,
                                pct_of_account  = EXCLUDED.pct_of_account,
                                cost_basis      = EXCLUDED.cost_basis,
                                avg_cost_basis  = EXCLUDED.avg_cost_basis,
                                ingested_at     = NOW()
                            """,
                            (
                                snap, acct, (row.get("Account Name") or "").strip(),
                                sym, normalize_ticker(sym),
                                (row.get("Description") or "").strip() or None,
                                _money(row.get("Quantity")),
                                _money(row.get("Last Price")),
                                _money(row.get("Current Value")),
                                _money(row.get("Today's Gain/Loss Dollar")),
                                _pct(row.get("Today's Gain/Loss Percent")),
                                _money(row.get("Total Gain/Loss Dollar")),
                                _pct(row.get("Total Gain/Loss Percent")),
                                _pct(row.get("Percent Of Account")),
                                _money(row.get("Cost Basis Total")),
                                _money(row.get("Average Cost Basis")),
                                (row.get("Type") or "").strip() or None,
                            ),
                        )
                        cur.execute("RELEASE SAVEPOINT row_sp")
                        summary["rows_upserted"] += 1
                    except Exception as e:
                        cur.execute("ROLLBACK TO SAVEPOINT row_sp")
                        summary["rows_failed"] += 1
                        if len(summary["errors"]) < 10:
                            summary["errors"].append(f"{sym}: {e!s}")
                try:
                    summary["positions_snapshot_rows"] = _snapshot_sync(
                        cur, snap, source="fidelity", crypto=False)
                except Exception as e:
                    summary["errors"].append(f"positions_snapshot sync: {e!s}")
                conn.commit()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="Path to Portfolio_Positions_*.csv")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    import json
    s = ingest(args.csv)
    print(json.dumps(s, indent=2, default=str))
    return 0 if s["rows_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
