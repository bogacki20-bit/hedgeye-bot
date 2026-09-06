"""composite_paper_log.py — nightly paper detector, signal_name='composite'
in signal_paper_fires (operator spec 8/29 round 2). LOGS ONLY: no alerts,
no trades, no Telegram, no REPORT, nothing on the live entry/exit path.

Entry event: ticker is on a Hedgeye roster as-of that day — Signal
Strength (ss_roster_history), Portfolio Solutions re-rank (the latest
hedgeye_portfolio_solutions snapshot on or before the day), or Position
Monitor LONG bucket (ticker_tags.hedgeye_bucket_0629 in active_long /
top_idea_long / long_bench) — AND the PRODUCTION keith_pattern fires on it
that same day (build_series + detect, loose/standard mode: exactly what
the live KEITH command runs; no reimplementation).
features: roster_sources (comma list, pm tagged with its bucket),
rp_at_fire, close.

Exit event: an SS drop (ss_flow_events event='drop') on a name with a
prior composite entry -> signal_name='composite_exit', fire_date = the
drop date. features: reason, entry dates it closes against.

Round-1 rationale: entry alpha is in NAME SELECTION, not dip timing — the
composite tests whether roster-gating the keith fire captures that.

Self-healing: each run processes every completed session in the trailing
CATCHUP_DAYS days (idempotent upserts). Caveat, declared: the Position
Monitor leg reads CURRENT ticker_tags state — PM bucket history is only
versioned in bucket_history — so a catch-up day may see a bucket up to
CATCHUP_DAYS newer than as-of. On the normal nightly cadence the lag is
zero.

    python -m tools.composite_paper_log            # nightly
    python -m tools.composite_paper_log --dry-run
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SIGNAL = "composite"
SIGNAL_EXIT = "composite_exit"
PM_LONG_BUCKETS = ("active_long", "top_idea_long", "long_bench")
CATCHUP_DAYS = 7


def _rp(price, lo, hi):
    if price is None or lo is None or hi is None or hi <= lo:
        return None
    return (float(price) - float(lo)) / (float(hi) - float(lo))


def recent_sessions(cur, days: int = CATCHUP_DAYS):
    from tools.trading_calendar import is_trading_day, last_completed_session
    lcs = last_completed_session(datetime.now())
    cur.execute("""SELECT DISTINCT snapshot_date FROM mfr_snapshots
                   WHERE snapshot_date >= %s ORDER BY snapshot_date""",
                (lcs - timedelta(days=days),))
    return [d for (d,) in cur.fetchall()
            if is_trading_day(d) and d <= lcs]


def rosters_asof(cur, session):
    """{ticker: [source, ...]} for the three roster legs as-of session."""
    out = {}
    try:
        cur.execute("""SELECT ticker FROM ss_roster_history
                       WHERE added_on <= %s
                         AND (removed_on IS NULL OR removed_on > %s)""",
                    (session, session))
        for (t,) in cur.fetchall():
            out.setdefault(t, []).append("ss")
    except Exception:
        pass
    try:
        cur.execute("""SELECT ticker FROM hedgeye_portfolio_solutions
                       WHERE snapshot_date = (SELECT max(snapshot_date)
                                              FROM hedgeye_portfolio_solutions
                                              WHERE snapshot_date <= %s)""",
                    (session,))
        for (t,) in cur.fetchall():
            out.setdefault(t, []).append("ps")
    except Exception:
        pass
    try:
        cur.execute("SELECT ticker, hedgeye_bucket_0629 FROM ticker_tags "
                    "WHERE hedgeye_bucket_0629 IN %s", (PM_LONG_BUCKETS,))
        for t, b in cur.fetchall():
            out.setdefault(t, []).append(f"pm:{b}")
    except Exception:
        pass
    return out


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    dry = "--dry-run" in argv
    import db_pg
    from tools.keith_pattern import build_series, detect
    from tools.signal_store import ensure_tables, upsert_fires
    with db_pg.get_conn() as c:
        cur = c.cursor()
        sessions = recent_sessions(cur)
        if not sessions:
            print("composite_paper_log: no completed sessions — nothing to do")
            return
        series = build_series(cur)

        # production detector, production mode; fires keyed by (ticker, day)
        fires_by_day = {}
        day_row = {}
        for t, rows in series.items():
            for r in rows:
                day_row[(t, r[0])] = r
            setups, _stage = detect(rows, entry_on_standing=True)
            for s in setups:
                fires_by_day.setdefault(s["date"], {})[t] = s

        total = 0
        for sess in sessions:
            rosters = rosters_asof(cur, sess)
            entries = []
            for t, s in fires_by_day.get(sess, {}).items():
                if t not in rosters:
                    continue
                _d, price, lo, hi, _tr = day_row[(t, sess)]
                rp = _rp(price, lo, hi)
                entries.append(("", t, sess, {
                    "roster_sources": ",".join(sorted(rosters[t])),
                    "rp_at_fire": round(rp, 4) if rp is not None else None,
                    "close": price,
                }))
            # exit events: SS drops on previously-logged names
            exits = []
            try:
                cur.execute("""SELECT e.ticker, min(f.fire_date)
                               FROM ss_flow_events e
                               JOIN signal_paper_fires f
                                 ON f.signal_name = %s
                                AND f.ticker = e.ticker
                                AND f.fire_date <= e.event_date
                               WHERE e.event = 'drop' AND e.event_date = %s
                               GROUP BY e.ticker""", (SIGNAL, sess))
                for t, first_entry in cur.fetchall():
                    exits.append(("", t, sess, {
                        "reason": "ss_drop",
                        "first_entry_date": str(first_entry),
                    }))
            except Exception as e:
                c.rollback()
                print(f"composite: exit scan failed for {sess}: {e}")

            if dry:
                print(f"composite DRY-RUN session={sess}: "
                      f"{len(entries)} entr(ies), {len(exits)} exit(s)")
                for _v, t, _d, f in entries + exits:
                    print(f"  {t}: {f}")
                continue
            ensure_tables(cur)
            n = upsert_fires(cur, SIGNAL, entries)
            n += upsert_fires(cur, SIGNAL_EXIT, exits)
            c.commit()      # commit per session so the exit join sees entries
            total += n
            print(f"composite session={sess}: {len(entries)} entr(ies), "
                  f"{len(exits)} exit(s)")
        if not dry:
            print(f"composite_paper_log: upserted {total} row(s) across "
                  f"{len(sessions)} session(s)")


if __name__ == "__main__":
    main()
