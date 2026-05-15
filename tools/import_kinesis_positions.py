"""Ingest a Kinesis Holdings_Statement CSV into portfolio_positions.

Schema (Kinesis export):
  Date, HIN, Account_Name, Asset, Amount, Total Value (C1USD)

One row per asset per snapshot. We map directly into portfolio_positions:
  account_number = HIN          (e.g. KM13868186)
  account_name   = 'Kinesis'    (so it shows up alongside Individual / IRAs)
  symbol         = Asset
  normalized_symbol = via ticker_aliases (BTC->BITCOIN, KAU->GOLD, ...)
  quantity       = Amount
  current_value  = Total Value (C1USD)
  account_type   = 'Crypto'
  snapshot_date  = Date::date

The new UNIQUE index from migration 006
(account_number, symbol, COALESCE(account_type,''), snapshot_date) handles
re-imports cleanly.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime, date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("import_kinesis_positions")


def _num(s):
    if s is None:
        return None
    t = str(s).strip()
    if t in ("", "--"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_date(s) -> date | None:
    if not s:
        return None
    s = str(s).strip().rstrip(" UTC")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def ingest(csv_path: str | Path) -> dict:
    import db_pg
    from tools.ticker_aliases import normalize_ticker

    p = Path(csv_path).expanduser().resolve()
    log.info("ingest: %s", p)
    summary = {"path": str(p), "rows_seen": 0, "rows_upserted": 0,
               "rows_failed": 0, "errors": []}

    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                for row in reader:
                    summary["rows_seen"] += 1
                    asset = (row.get("Asset") or "").strip().upper()
                    if not asset:
                        continue
                    snap = _parse_date(row.get("Date"))
                    if not snap:
                        summary["rows_failed"] += 1
                        summary["errors"].append(f"{asset}: unparseable date {row.get('Date')!r}")
                        continue
                    hin = (row.get("HIN") or "").strip()
                    qty = _num(row.get("Amount"))
                    val = _num(row.get("Total Value (C1USD)"))
                    cur.execute("SAVEPOINT row_sp")
                    try:
                        cur.execute(
                            """
                            INSERT INTO portfolio_positions (
                                snapshot_date, account_number, account_name,
                                symbol, normalized_symbol, description,
                                quantity, last_price, current_value,
                                account_type, ingested_at
                            ) VALUES (
                                %s, %s, %s,
                                %s, %s, %s,
                                %s, %s, %s,
                                %s, NOW()
                            )
                            ON CONFLICT (account_number, symbol,
                                         COALESCE(account_type, ''), snapshot_date)
                            DO UPDATE SET
                                quantity        = EXCLUDED.quantity,
                                current_value   = EXCLUDED.current_value,
                                last_price      = EXCLUDED.last_price,
                                ingested_at     = NOW()
                            """,
                            (
                                snap, hin or "Kinesis", "Kinesis",
                                asset, normalize_ticker(asset),
                                (row.get("Account_Name") or "").strip() or None,
                                qty,
                                (val / qty) if (qty and val and qty != 0) else None,
                                val,
                                "Crypto",
                            ),
                        )
                        cur.execute("RELEASE SAVEPOINT row_sp")
                        summary["rows_upserted"] += 1
                    except Exception as e:
                        cur.execute("ROLLBACK TO SAVEPOINT row_sp")
                        summary["rows_failed"] += 1
                        if len(summary["errors"]) < 10:
                            summary["errors"].append(f"{asset}: {e!s}")
                conn.commit()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="Path to Holdings_Statement_KM*.csv")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    s = ingest(args.csv)
    print(json.dumps(s, indent=2, default=str))
    return 0 if s["rows_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
