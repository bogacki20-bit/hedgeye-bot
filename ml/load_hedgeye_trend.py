"""ml/load_hedgeye_trend.py — build hedgeye_trend_daily (2c).

For every SPY trading day and every Risk Range instrument: the latest
TREND tag whose signal_date <= day, forward-filled at most 5 trading
days (an instrument off Keith's list stops carrying direction).
Rebuilds the table in place (DELETE + insert, one transaction).

    py ml/load_hedgeye_trend.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()
from psycopg2.extras import execute_values  # noqa: E402

MAX_STALE = 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT bar_date FROM px_daily WHERE ticker='SPY' "
                    "ORDER BY bar_date")
        days = [r[0] for r in cur.fetchall()]
        day_ix = {d: i for i, d in enumerate(days)}
        cur.execute("SELECT ticker, signal_date, trend FROM "
                    "hedgeye_risk_ranges WHERE trend IS NOT NULL "
                    "ORDER BY ticker, signal_date")
        sig = {}
        for t, d, tag in cur.fetchall():
            sig.setdefault(t, []).append((d, tag.upper()))

        from bisect import bisect_right
        rows = []
        for t, series in sig.items():
            j = 0
            last = None            # (signal_date, tag, index into days)
            for i, d in enumerate(days):
                while j < len(series) and series[j][0] <= d:
                    sd, tag = series[j]
                    # weekend/holiday signal dates anchor to the previous
                    # trading day for the staleness count
                    ix = day_ix.get(sd)
                    if ix is None:
                        ix = max(0, bisect_right(days, sd) - 1)
                    last = (sd, tag, ix)
                    j += 1
                if last is None:
                    continue
                stale = i - last[2]
                if 0 <= stale <= MAX_STALE:
                    rows.append((d, t, last[1], last[0], stale))
        print(f"{len(rows)} rows across {len(sig)} instruments, "
              f"{days[0]} .. {days[-1]}")
        if args.dry_run:
            return 0
        cur.execute("DELETE FROM hedgeye_trend_daily")
        for i in range(0, len(rows), 5000):
            execute_values(cur,
                "INSERT INTO hedgeye_trend_daily "
                "(date, ticker, tag, last_signal_date, staleness) VALUES %s",
                rows[i:i + 5000], page_size=5000)
        conn.commit()
        cur.execute("SELECT tag, count(*) FROM hedgeye_trend_daily GROUP BY tag")
        print("tags:", dict(cur.fetchall()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
