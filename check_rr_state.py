"""Quick diagnostic — for a list of tickers, show which have Risk Range / ETF Pro Range / MFR data and how fresh."""
import sys
import json
from datetime import datetime, date, timedelta

import db_pg


def check(ticker: str) -> dict:
    out = {"ticker": ticker.upper(), "risk_range": None,
           "etf_pro_range": None, "mfr": None}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT signal_date, buy_trade, sell_trade FROM hedgeye_risk_ranges "
                "WHERE ticker=%s ORDER BY signal_date DESC LIMIT 1",
                (ticker.upper(),),
            )
            row = cur.fetchone()
            if row:
                out["risk_range"] = {
                    "signal_date": str(row[0]),
                    "buy_trade":   float(row[1]) if row[1] else None,
                    "sell_trade":  float(row[2]) if row[2] else None,
                    "age_days":    (date.today() - row[0]).days if row[0] else None,
                }

            cur.execute(
                "SELECT week_of, range_low, range_high FROM hedgeye_etf_pro_ranges "
                "WHERE ticker=%s ORDER BY week_of DESC LIMIT 1",
                (ticker.upper(),),
            )
            row = cur.fetchone()
            if row:
                out["etf_pro_range"] = {
                    "week_of":    str(row[0]),
                    "range_low":  float(row[1]) if row[1] else None,
                    "range_high": float(row[2]) if row[2] else None,
                    "age_days":   (date.today() - row[0]).days if row[0] else None,
                }

            cur.execute(
                "SELECT snapshot_date, range_low, range_high, hurst FROM mfr_snapshots "
                "WHERE ticker=%s ORDER BY snapshot_date DESC, fetched_at DESC LIMIT 1",
                (ticker.upper(),),
            )
            row = cur.fetchone()
            if row:
                out["mfr"] = {
                    "snapshot_date": str(row[0]),
                    "range_low":     float(row[1]) if row[1] else None,
                    "range_high":    float(row[2]) if row[2] else None,
                    "hurst":         float(row[3]) if row[3] else None,
                    "age_days":      (date.today() - row[0]).days if row[0] else None,
                }
    return out


if __name__ == "__main__":
    tickers = sys.argv[1:] or ["OIH", "XOP", "SPY", "XLE", "QQQ"]
    results = [check(t) for t in tickers]
    print(json.dumps(results, indent=2, default=str), flush=True)
