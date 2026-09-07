"""ml/build_targets.py — Round-2 Phase A Step 2: ml_targets (+ rv20 feature).

Targets from bars STRICTLY after D, entry reference = close[D]:
  fwd_ret_{10,20,30}  close[D+N]/close[D] - 1
  fwd_mfe_20 / fwd_mae_20  max(high)/min(low) excursion over D+1..D+20
  rr_hit_5_2p5_30     TP +5% before SL -2.5% within 30 bars, CONSERVATIVE:
                      per bar the SL is checked first, so a bar touching both
                      counts as SL (TrendSpider "conservative mode"). NULL
                      when end-of-data truncates the window unresolved.
  fwd_sharpe_20       fwd_ret_20 / rv20(D) — the primary ranking target.

rv20(D) = stdev (ddof=1, unannualized) of the trailing 20 daily close
returns ending at D. Stored BOTH as the sharpe denominator and as an
ml_features.rv20 feature (operator addition, README_SEAN.md) — it is
computed from bars <= D only, so it is a legitimate feature.

    py ml/build_targets.py            # upsert + validation report
    py ml/build_targets.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

from ml.universe import TICKERS  # noqa: E402

TP, SL, RR_WIN = 0.05, 0.025, 30


def rr_hit(highs, lows, entry):
    """1 TP-first, 0 SL-first-or-expired, None window truncated unresolved.
    highs/lows: the next RR_WIN bars (may be shorter at end of data)."""
    for h, l in zip(highs, lows):
        if l == l and l <= entry * (1 - SL):
            return 0.0
        if h == h and h >= entry * (1 + TP):
            return 1.0
    return 0.0 if len(highs) >= RR_WIN else None


def build(t, px):
    f = px[px.ticker == t].set_index("bar_date").sort_index()
    c, h, l = f["c"], f["h"], f["l"]
    ret = c.pct_change()
    rv20 = ret.rolling(20, min_periods=20).std(ddof=1)
    out = pd.DataFrame(index=f.index)
    for n in (10, 20, 30):
        out[f"fwd_ret_{n}"] = c.shift(-n) / c - 1
    out["fwd_mfe_20"] = h.shift(-1).rolling(20, min_periods=20).max().shift(-19) / c - 1
    out["fwd_mae_20"] = l.shift(-1).rolling(20, min_periods=20).min().shift(-19) / c - 1
    hits = []
    hv, lv, cv = h.tolist(), l.tolist(), c.tolist()
    for i in range(len(cv)):
        hits.append(rr_hit(hv[i + 1: i + 1 + RR_WIN], lv[i + 1: i + 1 + RR_WIN], cv[i]))
    out["rr_hit_5_2p5_30"] = hits
    out["rv20"] = rv20
    out["fwd_sharpe_20"] = (out["fwd_ret_20"] / rv20).where(rv20 > 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with db_pg.get_conn() as conn:
        px = pd.read_sql(
            "SELECT ticker, bar_date, h, l, c FROM px_daily "
            "WHERE ticker = ANY(%s) AND source='tradingview'",
            conn, params=(TICKERS,))
    px["bar_date"] = pd.to_datetime(px["bar_date"])
    for col in ("h", "l", "c"):
        px[col] = px[col].astype(float)

    tcols = ["fwd_ret_10", "fwd_ret_20", "fwd_ret_30", "fwd_mfe_20",
             "fwd_mae_20", "rr_hit_5_2p5_30", "fwd_sharpe_20"]
    rows, rvrows = [], []
    print(f"{'ticker':<7} {'rows':>5}  rr base%   fwd_ret_20 min/max   "
          f"sharpe20 min/max   NULL-tail(fwd20)")
    for t in TICKERS:
        o = build(t, px)
        rr = o["rr_hit_5_2p5_30"]
        print(f"{t:<7} {len(o):>5}  {rr.mean()*100:6.1f}%   "
              f"{o['fwd_ret_20'].min():+.4f}/{o['fwd_ret_20'].max():+.4f}   "
              f"{o['fwd_sharpe_20'].min():+.2f}/{o['fwd_sharpe_20'].max():+.2f}   "
              f"{int(o['fwd_ret_20'].isna().sum())}")
        for d, r in o.iterrows():
            vals = [None if (r[k] != r[k]) else float(r[k]) for k in tcols]
            rows.append((t, d.date(), *vals))
            if r["rv20"] == r["rv20"]:
                rvrows.append((t, d.date(), float(r["rv20"])))
    if args.dry_run:
        print(f"[dry-run] {len(rows)} target rows, {len(rvrows)} rv20 updates")
        return 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), 1000):
            execute_values(
                cur,
                "INSERT INTO ml_targets (ticker, bar_date, " + ", ".join(tcols)
                + ") VALUES %s ON CONFLICT (ticker, bar_date) DO UPDATE SET "
                + ", ".join(f"{k}=EXCLUDED.{k}" for k in tcols)
                + ", computed_at=now()",
                rows[i:i + 1000], page_size=1000)
            conn.commit()
        for i in range(0, len(rvrows), 1000):
            execute_values(
                cur,
                "UPDATE ml_features AS m SET rv20 = d.rv20 "
                "FROM (VALUES %s) AS d(ticker, bar_date, rv20) "
                "WHERE m.ticker = d.ticker AND m.bar_date = d.bar_date",
                rvrows[i:i + 1000], page_size=1000)
            conn.commit()
        cur.execute("SELECT count(*) FROM ml_targets")
        n1 = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM ml_features WHERE rv20 IS NOT NULL")
        n2 = cur.fetchone()[0]
        print(f"ml_targets: {n1} rows; ml_features.rv20 set on {n2} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
