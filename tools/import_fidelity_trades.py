"""Ingest a Fidelity Accounts_History CSV into actions_log (PG).

Schema (Fidelity export):
  Run Date, Account, Account Number, Action, Symbol, Description, Type,
  Exchange Quantity, Exchange Currency, Currency, Price, Quantity,
  Exchange Rate, Commission, Fees, Accrued Interest, Amount, Settlement Date

Only rows whose Action begins with 'YOU BOUGHT' or 'YOU SOLD' are kept.
Debit-card purchases (Symbol empty) and journals / transfers are dropped.

Idempotency: composite UNIQUE on
(run_date, account_number, raw_symbol, qty, price, amount) — re-ingest is safe.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("import_fidelity_trades")

TRADE_PREFIXES = ("YOU BOUGHT", "YOU SOLD")


def _money(s: str | None) -> float | None:
    if s is None:
        return None
    t = str(s).strip().replace("$", "").replace(",", "").replace("+", "")
    if t in ("", "--"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _date(s: str | None):
    if not s:
        return None
    s = s.strip().strip('"').strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _is_trade(action: str) -> bool:
    a = (action or "").strip().upper()
    return any(a.startswith(p) for p in TRADE_PREFIXES)


def ingest(csv_path: str | Path) -> dict:
    import db_pg
    from psycopg2.extras import Json
    from tools.ticker_aliases import normalize_ticker

    p = Path(csv_path).expanduser().resolve()
    log.info("ingest: %s", p)

    summary = {"path": str(p), "rows_seen": 0, "rows_non_trade_skipped": 0,
               "rows_inserted": 0, "rows_dup_skipped": 0, "rows_failed": 0,
               "errors": []}

    # Fidelity exports have 1-3 preamble lines before the header. Skip until
    # we find a line that starts with "Run Date".
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        # Read raw, find header offset
        lines = f.readlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("Run Date"):
            header_idx = i
            break
    if header_idx is None:
        summary["errors"].append("could not find 'Run Date' header row")
        return summary

    reader = csv.DictReader(lines[header_idx:])
    _quad_cache: dict = {}   # run_date -> (monthly, quarterly). One lookup per
    #                          distinct date on the EXISTING connection — the
    #                          old per-row db_pg.get_conn() re-dialed Railway
    #                          1400+ times per ingest (20+ min of handshakes).
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for row in reader:
                summary["rows_seen"] += 1
                action = (row.get("Action") or "").strip()
                if not _is_trade(action):
                    summary["rows_non_trade_skipped"] += 1
                    continue
                raw_sym = (row.get("Symbol") or "").strip().upper() or None
                if not raw_sym:
                    summary["rows_non_trade_skipped"] += 1
                    continue
                run_date = _date(row.get("Run Date"))
                settle_date = _date(row.get("Settlement Date"))
                acct = (row.get("Account Number") or "").strip().strip('"')
                # Quad regime stamping (2026-05-28): look up the regime
                # effective on the trade's run_date so historical CSV
                # ingests retroactively land in the right Quad slice.
                # Falls through to NULLs if the helper / table isn't
                # available (pre-migration env).
                _mq = _qq = None
                try:
                    if run_date not in _quad_cache:
                        with conn.cursor() as _qcur:
                            _qcur.execute(
                                """
                                SELECT monthly_quad, quarterly_quad
                                  FROM quad_regime_history
                                 WHERE effective_at <= %s::timestamp
                                                      + interval '23 hours 59 minutes'
                                 ORDER BY effective_at DESC LIMIT 1
                                """,
                                (run_date,),
                            )
                            _quad_cache[run_date] = _qcur.fetchone() or (None, None)
                    _mq, _qq = _quad_cache[run_date]
                except Exception:
                    _quad_cache[run_date] = (None, None)
                try:
                    cur.execute(
                        """
                        INSERT INTO actions_log (
                            run_date, account_name, account_number,
                            action, raw_symbol, normalized_symbol,
                            qty, price, amount, commission, fees,
                            type, source, raw_row, settlement_date,
                            monthly_quad, quarterly_quad
                        ) VALUES (
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s
                        )
                        ON CONFLICT (run_date, account_number,
                                     COALESCE(raw_symbol, ''),
                                     COALESCE(qty, 0),
                                     COALESCE(price, 0),
                                     COALESCE(amount, 0))
                        DO NOTHING
                        """,
                        (
                            run_date,
                            (row.get("Account") or "").strip().strip('"') or None,
                            acct,
                            action,
                            raw_sym, normalize_ticker(raw_sym),
                            _money(row.get("Quantity")),
                            _money(row.get("Price")),
                            _money(row.get("Amount")),
                            _money(row.get("Commission")),
                            _money(row.get("Fees")),
                            (row.get("Type") or "").strip() or None,
                            "fidelity",
                            Json({k: v for k, v in row.items() if v not in (None, "")}),
                            settle_date,
                            _mq, _qq,
                        ),
                    )
                    if cur.rowcount == 1:
                        summary["rows_inserted"] += 1
                    else:
                        summary["rows_dup_skipped"] += 1
                except Exception as e:
                    summary["rows_failed"] += 1
                    if len(summary["errors"]) < 10:
                        summary["errors"].append(f"{raw_sym} {run_date}: {e!s}")
            conn.commit()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="Path to Accounts_History*.csv")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    s = ingest(args.csv)
    print(json.dumps(s, indent=2, default=str))
    return 0 if s["rows_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
