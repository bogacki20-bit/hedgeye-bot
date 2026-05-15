"""Ingest a Kinesis Transactions CSV into actions_log.

Schema (Kinesis export):
  DateTime, HIN, Transactions_ID, Order_ID, Currency_Pair, Transaction_Type,
  Amount, Amount_Currency, Trade_Price, Trade_Price_Currency, Total, Fee,
  Fee_Currency, Trade_Value_in_C1USD

Each trade is TWO rows sharing the same Transactions_ID — one 'incoming',
one 'outgoing'. We collapse each pair into a single actions_log row:
  - If the asset side is incoming (asset !=  C1USD) → BUY of the asset.
        qty   = +incoming.Amount
        price = Trade_Price
        amount = -outgoing.Amount  (cash out)
  - If the asset side is outgoing → SELL of the asset.
        qty   = -outgoing.Amount
        amount = +incoming.Amount  (cash in)

Inserts use the same composite UNIQUE as the Fidelity importer
(run_date, account_number, raw_symbol, qty, price, amount) so re-imports
are no-ops.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("import_kinesis_trades")

CASH_TOKENS = {"C1USD"}   # Anything else is treated as an asset


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


def _parse_dt(s):
    if not s:
        return None
    s = s.strip().rstrip(" UTC")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def ingest(csv_path: str | Path) -> dict:
    import db_pg
    from psycopg2.extras import Json
    from tools.ticker_aliases import normalize_ticker

    p = Path(csv_path).expanduser().resolve()
    log.info("ingest: %s", p)
    summary = {"path": str(p), "rows_seen": 0, "pairs": 0,
               "rows_inserted": 0, "rows_dup_skipped": 0,
               "rows_failed": 0, "errors": []}

    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # Group rows by Transactions_ID
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in reader:
            summary["rows_seen"] += 1
            tid = (row.get("Transactions_ID") or "").strip()
            if tid:
                groups[tid].append(row)

    summary["pairs"] = len(groups)

    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            for tid, legs in groups.items():
                incoming = next((r for r in legs if (r.get("Transaction_Type") or "").strip().lower() == "incoming"), None)
                outgoing = next((r for r in legs if (r.get("Transaction_Type") or "").strip().lower() == "outgoing"), None)
                if not incoming or not outgoing:
                    summary["rows_failed"] += 1
                    if len(summary["errors"]) < 10:
                        summary["errors"].append(f"tid {tid}: missing incoming/outgoing leg")
                    continue

                in_ccy  = (incoming.get("Amount_Currency") or "").strip().upper()
                out_ccy = (outgoing.get("Amount_Currency") or "").strip().upper()
                in_amt  = _num(incoming.get("Amount"))  or 0.0
                out_amt = _num(outgoing.get("Amount")) or 0.0
                price   = _num(incoming.get("Trade_Price")) or _num(outgoing.get("Trade_Price"))
                fee     = _num(incoming.get("Fee")) or _num(outgoing.get("Fee"))
                dt      = _parse_dt(incoming.get("DateTime") or outgoing.get("DateTime"))
                run_date = dt.date() if dt else None
                hin = (incoming.get("HIN") or outgoing.get("HIN") or "").strip()
                pair = (incoming.get("Currency_Pair") or "").strip()

                # Decide direction by which side is the asset (non-C1USD).
                if in_ccy not in CASH_TOKENS and out_ccy in CASH_TOKENS:
                    # BUY of the asset
                    raw_sym = in_ccy
                    qty = in_amt
                    amount = -out_amt   # cash out
                    action_text = f"YOU BOUGHT {raw_sym} ({pair})"
                elif out_ccy not in CASH_TOKENS and in_ccy in CASH_TOKENS:
                    # SELL of the asset
                    raw_sym = out_ccy
                    qty = -out_amt
                    amount = in_amt     # cash in
                    action_text = f"YOU SOLD {raw_sym} ({pair})"
                else:
                    # asset<->asset or unknown — record but flag
                    raw_sym = in_ccy or out_ccy or pair
                    qty = in_amt
                    amount = _num(incoming.get("Trade_Value_in_C1USD"))
                    action_text = f"YOU TRADED {pair}"

                try:
                    cur.execute(
                        """
                        INSERT INTO actions_log (
                            run_date, account_name, account_number,
                            action, raw_symbol, normalized_symbol,
                            qty, price, amount, commission, fees,
                            type, source, raw_row, settlement_date
                        ) VALUES (
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s
                        )
                        ON CONFLICT (run_date, account_number,
                                     COALESCE(raw_symbol, ''),
                                     COALESCE(qty, 0),
                                     COALESCE(price, 0),
                                     COALESCE(amount, 0))
                        DO NOTHING
                        """,
                        (
                            run_date, "Kinesis", hin or "Kinesis",
                            action_text, raw_sym, normalize_ticker(raw_sym),
                            qty, price, amount, None, fee,
                            "Crypto",
                            "kinesis_csv",
                            Json({"tid": tid, "pair": pair,
                                  "incoming": {k: v for k, v in incoming.items() if v not in (None, "")},
                                  "outgoing": {k: v for k, v in outgoing.items() if v not in (None, "")}}),
                            None,
                        ),
                    )
                    if cur.rowcount == 1:
                        summary["rows_inserted"] += 1
                    else:
                        summary["rows_dup_skipped"] += 1
                except Exception as e:
                    summary["rows_failed"] += 1
                    if len(summary["errors"]) < 10:
                        summary["errors"].append(f"tid {tid}: {e!s}")
            conn.commit()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", help="Path to Transactions_Statement_KM*.csv")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    s = ingest(args.csv)
    print(json.dumps(s, indent=2, default=str))
    return 0 if s["rows_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
