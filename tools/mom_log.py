"""mom_log.py — daily momentum-print log for the desk's corridor decode
(9/20 ask: settle the V miss with a month of data, not five names on one
date).

Regenerates outputs/project_knowledge/mom_log.csv from history on every
run (mfr_snapshots already stores the label per name per day, so the
month of prints exists TODAY). Columns:

  date, ticker, mom, close, d_bull, d_bear, h_close, h_bull, h_bear

d_bull/d_bear = the DAILY corridor from tv_mfr_history where a fresh-
enough TV export covers the name. The h_* columns are EMPTY by design:
no hourly corridor exists in any feed the bot pulls (the MFR API ships
labels only; hourly lives solely in TradingView). They are in the header
so the file's shape is final — they fill the day an hourly source lands.

Universe: Position Monitor names + held names (full MFR universe would
be ~200K rows of noise). Runs daily from the 9:40 desk-note task.
"""

from __future__ import annotations

import pathlib

OUT = (pathlib.Path(__file__).resolve().parent.parent
       / "outputs" / "project_knowledge" / "mom_log.csv")
SINCE_DAYS = 45


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def write_log() -> pathlib.Path:
    rows = _rows("""
        WITH uni AS (
            SELECT ticker FROM ticker_tags WHERE hedgeye_bucket_0629 IS NOT NULL
            UNION
            SELECT DISTINCT underlying FROM book_positions
            WHERE snapshot_date=(SELECT max(snapshot_date) FROM book_positions)
              AND asset_class <> 'cash')
        SELECT m.snapshot_date, m.ticker,
               CASE m.momentum_signal
                    WHEN 'momentumBullish' THEN 'BULL'
                    WHEN 'momentumBearish' THEN 'BEAR'
                    WHEN 'momentumNeutral' THEN 'NEUT'
                    WHEN 'momentumNeutralDanger' THEN 'NEUT-DANGER'
                    ELSE m.momentum_signal END,
               m.price, tv.bull_level, tv.bear_level
        FROM mfr_snapshots m
        JOIN uni u ON u.ticker = m.ticker
        LEFT JOIN LATERAL (
            SELECT bull_level, bear_level FROM tv_mfr_history t
            WHERE t.ticker = m.ticker AND t.bar_date <= m.snapshot_date
              AND t.bar_date >= m.snapshot_date - 5
            ORDER BY t.bar_date DESC LIMIT 1) tv ON TRUE
        WHERE m.snapshot_date >= CURRENT_DATE - %s
          AND m.momentum_signal IS NOT NULL
        ORDER BY m.snapshot_date, m.ticker""", (SINCE_DAYS,))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write("date,ticker,mom,close,d_bull,d_bear,h_close,h_bull,h_bear\n")
        for d, t, mom, px, bl, br in rows:
            f.write(f"{d},{t},{mom},"
                    f"{'' if px is None else format(float(px), 'g')},"
                    f"{'' if bl is None else format(float(bl), 'g')},"
                    f"{'' if br is None else format(float(br), 'g')},,,\n")
    return OUT


if __name__ == "__main__":
    import sys
    for _s in (sys.stdout,):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = write_log()
    print("wrote", p, f"({p.stat().st_size // 1024} KB)")
