"""trend_daily_sync.py — materialize the as-of trend state per name per day
into the trend_daily table (operator spec 8/29 round 2, item 1a).

The trend was previously derived on the fly inside every consumer
(keith_pattern.build_series: MFR trend_signal with the Hedgeye RR overlay,
RR authoritative where the name is in that day's RR email). This tool
materializes exactly that derivation — same function, no re-implementation
— so range-floor breaks can be split by trend state with one join.

A row with NULL trend = the name was polled that day but no trend was
known (declared, not hidden). Backfill is legitimate here because every
input is an as-of-date stored row; recomputing June's trend today reads
only June's data.

    python -m tools.trend_daily_sync --backfill   # full history, run once
    python -m tools.trend_daily_sync              # nightly: trailing 7 days

LOGS ONLY table maintenance: no alerts, no REPORT, nothing on the live
entry/exit path reads trend_daily.
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NIGHTLY_WINDOW_DAYS = 7


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    backfill = "--backfill" in argv
    import db_pg
    from tools.keith_pattern import build_series
    from tools.signal_store import ensure_tables, upsert_trend
    with db_pg.get_conn() as c:
        cur = c.cursor()
        ensure_tables(cur)
        series = build_series(cur)
        cutoff = None
        if not backfill:
            cur.execute("SELECT max(date) FROM trend_daily")
            mx = cur.fetchone()[0]
            if mx:
                cutoff = mx - timedelta(days=NIGHTLY_WINDOW_DAYS)
        rows = [(t, d, trend)
                for t, rws in series.items()
                for d, _p, _lo, _hi, trend in rws
                if cutoff is None or d >= cutoff]
        n = upsert_trend(cur, rows)
        c.commit()
        span = (min(r[1] for r in rows), max(r[1] for r in rows)) if rows else None
        print(f"trend_daily_sync: upserted {n} row(s) "
              f"({'FULL BACKFILL' if cutoff is None else f'window >= {cutoff}'}"
              f", span {span})")


if __name__ == "__main__":
    main()
