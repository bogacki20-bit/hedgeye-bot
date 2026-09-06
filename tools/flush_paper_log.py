"""flush_paper_log.py — nightly paper detector, signal_name='flush' in
signal_paper_fires (operator spec 8/29 round 2; exit-study candidate,
HIGHEST priority of the paper signals). LOGS ONLY: no alerts, no trades,
no Telegram, no REPORT, nothing on the live entry/exit path.

Every evening, flag any name in the polling universe that
  (a) closed down >= 5% over the trailing 5 sessions, AND
  (b) sits in the bottom 10% of its trailing 20-session close range,
and upsert one signal_paper_fires row per match. ALL matches are logged,
universe-wide, held or not — the non-held names are the control
population for the held ones (round-1 finding: Keith-dropped names at the
range floor underperform, generic floor breaks mostly recover; this log
accrues the out-of-sample data to find the discriminator).

features jsonb: ret_5d, pos_in_20d_range, vol_ratio_5v15, trend, in_book,
on_ss, close.

Definitions (declared, not fitted):
  session      an mfr snapshot_date that is a completed NYSE session
               (tools.trading_calendar; a 24/7 instrument's weekend or
               future bar must not define a session).
  ret_5d       close[s] / close[5 sessions earlier] - 1, from the
               ticker's own mfr close series.
  pos_in_20d_range  (close - min20) / (max20 - min20) over the trailing
               20 session closes incl. s (needs >= 20 rows; degenerate
               range -> skipped, counted in the summary line).
  vol_ratio_5v15  last-5-session avg volume / prior-15-session avg. MFR
               publishes PREVIOUS-day volume, so the series lags one
               session (declared; null when < 20 aligned volumes).
  trend        the trend the KEITH detector sees that day — MFR trend
               with the Hedgeye RR overlay (keith_pattern.build_series).
  in_book      latest book_positions snapshot <= session holds the name.
  on_ss        ss_roster_history membership as-of session.

Self-healing: each run processes EVERY completed session in the trailing
CATCHUP_DAYS calendar days (idempotent upserts), so a missed night is
recovered on the next run — the signal is a pure function of stored
as-of rows, so late computation is not look-ahead.

    python -m tools.flush_paper_log            # nightly (Task Scheduler)
    python -m tools.flush_paper_log --dry-run
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SIGNAL = "flush"
RET5_MAX = -0.05        # (a) trailing-5-session return threshold
POS20_MAX = 0.10        # (b) bottom decile of the trailing 20d close range
CATCHUP_DAYS = 7


def recent_sessions(cur, days: int = CATCHUP_DAYS):
    """Completed NYSE sessions with mfr rows in the trailing N calendar
    days, ascending."""
    from tools.trading_calendar import is_trading_day, last_completed_session
    lcs = last_completed_session(datetime.now())
    cur.execute("""SELECT DISTINCT snapshot_date FROM mfr_snapshots
                   WHERE snapshot_date >= %s
                   ORDER BY snapshot_date""",
                (lcs - timedelta(days=days),))
    return [d for (d,) in cur.fetchall()
            if is_trading_day(d) and d <= lcs]


def compute_rows(cur, session, hist, trend_series):
    """Matches for one session from preloaded history. Returns (rows,
    skipped): rows are (variant, ticker, fire_date, features) tuples."""
    trend_at = {}
    for t, rows in trend_series.items():
        for d, _p, _lo, _hi, tr in rows:
            if d == session:
                trend_at[t] = tr

    book_syms = set()
    try:
        cur.execute("SELECT max(snapshot_date) FROM book_positions "
                    "WHERE snapshot_date <= %s", (session,))
        bd = cur.fetchone()[0]
        if bd:
            cur.execute("""SELECT DISTINCT upper(coalesce(underlying, symbol))
                           FROM book_positions WHERE snapshot_date = %s
                             AND quantity IS NOT NULL AND quantity <> 0""",
                        (bd,))
            book_syms = {r[0] for r in cur.fetchall()}
    except Exception:
        pass

    ss_now = set()
    try:
        cur.execute("""SELECT ticker FROM ss_roster_history
                       WHERE added_on <= %s
                         AND (removed_on IS NULL OR removed_on > %s)""",
                    (session, session))
        ss_now = {r[0] for r in cur.fetchall()}
    except Exception:
        pass

    out, skipped = [], defaultdict(int)
    for t, rows in hist.items():
        upto = [r for r in rows if r[0] <= session]
        if not upto or upto[-1][0] != session:
            skipped["no_row_at_session"] += 1
            continue
        closes = [r[1] for r in upto]
        if len(closes) < 20:        # subsumes the 6-row 5d-return need
            skipped["lt20_bars"] += 1
            continue
        c = closes[-1]
        ret5 = c / closes[-6] - 1.0
        win20 = closes[-20:]
        lo20, hi20 = min(win20), max(win20)
        if hi20 <= lo20:
            skipped["degenerate_range"] += 1
            continue
        pos20 = (c - lo20) / (hi20 - lo20)
        if not (ret5 <= RET5_MAX and pos20 <= POS20_MAX):
            continue

        # aligned volume series: vol for upto[i-1] comes from upto[i]
        vols = [upto[i][2] for i in range(1, len(upto))
                if upto[i][2] is not None
                and (upto[i][0] - upto[i - 1][0]).days <= 4]
        vratio = None
        if len(vols) >= 20:
            p15 = sum(vols[-20:-5]) / 15.0
            if p15 > 0:
                vratio = round((sum(vols[-5:]) / 5.0) / p15, 4)

        out.append(("", t, session, {
            "ret_5d": round(ret5, 4),
            "pos_in_20d_range": round(pos20, 4),
            "vol_ratio_5v15": vratio,
            "trend": trend_at.get(t),
            "in_book": t.upper() in book_syms,
            "on_ss": t in ss_now,
            "close": c,
        }))
    return out, dict(skipped)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    dry = "--dry-run" in argv
    import db_pg
    from tools.keith_pattern import build_series
    from tools.signal_store import ensure_tables, upsert_fires
    with db_pg.get_conn() as c:
        cur = c.cursor()
        sessions = recent_sessions(cur)
        if not sessions:
            print("flush_paper_log: no completed sessions in window — "
                  "nothing to do")
            return
        cur.execute("""SELECT ticker, snapshot_date, price::float,
                              previous_day_volume
                       FROM mfr_snapshots
                       WHERE price IS NOT NULL AND snapshot_date <= %s
                       ORDER BY ticker, snapshot_date""", (sessions[-1],))
        hist = defaultdict(list)
        for t, d, px, pdv in cur.fetchall():
            hist[t].append((d, px, pdv))
        trend_series = build_series(cur)

        total = 0
        for s in sessions:
            rows, skipped = compute_rows(cur, s, hist, trend_series)
            if dry:
                print(f"flush DRY-RUN session={s}: {len(rows)} match(es), "
                      f"skipped={skipped}")
                for _v, t, _d, f in sorted(rows,
                                           key=lambda x: x[3]["pos_in_20d_range"]):
                    print(f"  {t}: " + " ".join(f"{k}={v}"
                                                for k, v in f.items()))
                continue
            ensure_tables(cur)
            total += upsert_fires(cur, SIGNAL, rows)
            print(f"flush session={s}: {len(rows)} match(es), "
                  f"skipped={skipped}")
        if not dry:
            c.commit()
            print(f"flush_paper_log: upserted {total} row(s) across "
                  f"{len(sessions)} session(s)")


if __name__ == "__main__":
    main()
