"""ml/rule_report.py — rule-only performance, NO ML (operator ask 2026-09-07).

Over the walk-forward evaluation years (2021-2026 YTD) only, for
  setup_lrr  : above_trend==1 AND rp<0.45
  setup_dip  : setup_lrr AND decel_streak>=1
  setup_dip2 : setup_lrr AND decel_streak>=2
vs setup_any (every bar with defined rp): hit rate, mean fwd_ret_20, mean
fwd_sharpe_20, n — per ticker, per year, and pooled — plus a bootstrap 90%
CI on the hit-rate DIFFERENCE vs setup_any (10,000 resamples). This
isolates what the process rule is worth by itself, so the ML's
contribution is measured against the right baseline.

    py ml/rule_report.py [--tickers SPY,UUP,...]   (default: Phase A five)
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
from ml.universe import PHASE_A  # noqa: E402

YEARS = list(range(2021, 2027))
NBOOT = 10_000


def load(tickers):
    with db_pg.get_conn() as conn:
        f = pd.read_sql(
            "SELECT f.ticker, f.bar_date, f.rp, f.above_trend, "
            "f.decel_streak, t.fwd_ret_20, t.fwd_sharpe_20 "
            "FROM ml_features f JOIN ml_targets t USING (ticker, bar_date) "
            "WHERE f.ticker = ANY(%s) AND t.fwd_ret_20 IS NOT NULL",
            conn, params=(tickers,))
    f["bar_date"] = pd.to_datetime(f["bar_date"])
    for c in f.columns[2:]:
        f[c] = f[c].astype(float)
    f["year"] = f["bar_date"].dt.year
    return f[f["year"].isin(YEARS) & f["rp"].notna()].reset_index(drop=True)


def rules(f):
    lrr = (f["above_trend"] == 1) & (f["rp"] < 0.45)
    return {"setup_lrr": lrr,
            "setup_dip": lrr & (f["decel_streak"] >= 1),
            "setup_dip2": lrr & (f["decel_streak"] >= 2)}


def stats(g):
    r = g["fwd_ret_20"]
    return (len(g), float((r > 0).mean()), float(r.mean()),
            float(g["fwd_sharpe_20"].mean()))


def boot_ci(rule_hits, any_hits, rng):
    """90% CI on hit(rule) - hit(any), independent resampling."""
    n_r, n_a = len(rule_hits), len(any_hits)
    if n_r < 5:
        return None
    diffs = (rng.choice(rule_hits, (NBOOT, n_r)).mean(axis=1)
             - rng.choice(any_hits, (NBOOT, n_a)).mean(axis=1))
    return float(np.percentile(diffs, 5)), float(np.percentile(diffs, 95))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=",".join(PHASE_A))
    args = ap.parse_args()
    tickers = args.tickers.split(",")
    f = load(tickers)
    rng = np.random.default_rng(7)
    masks = rules(f)
    any_hits = (f["fwd_ret_20"] > 0).astype(float).to_numpy()
    n, hit, mret, msh = stats(f)
    print(f"RULE-ONLY REPORT — eval years {YEARS[0]}-{YEARS[-1]}, "
          f"tickers {','.join(tickers)}")
    print(f"\nsetup_any (baseline): n={n}  hit={hit*100:.1f}%  "
          f"fwd_ret_20={mret*100:+.2f}%  fwd_sharpe_20={msh:+.3f}")
    for name, m in masks.items():
        d = f[m]
        n, hit, mret, msh = stats(d)
        ci = boot_ci((d["fwd_ret_20"] > 0).astype(float).to_numpy(),
                     any_hits, rng)
        ci_s = (f"[{ci[0]*100:+.1f}, {ci[1]*100:+.1f}] pts"
                if ci else "n<5")
        print(f"\n== {name}: n={n}  hit={hit*100:.1f}%  "
              f"fwd_ret_20={mret*100:+.2f}%  fwd_sharpe_20={msh:+.3f}  "
              f"hit-diff vs any 90% CI {ci_s}")
        print(f"   {'':<6} " + "  ".join(f"{y:>16}" for y in YEARS))
        for t in tickers:
            cells = []
            for y in YEARS:
                g = d[(d.ticker == t) & (d.year == y)]
                if len(g) == 0:
                    cells.append(f"{'—':>16}")
                else:
                    _n, h, r, _s = stats(g)
                    cells.append(f"{_n:>3} {h*100:4.0f}% {r*100:+5.1f}%")
            print(f"   {t:<6} " + "  ".join(cells))
        # per-ticker pooled with CI
        for t in tickers:
            g = d[d.ticker == t]
            ga = f[f.ticker == t]
            if len(g) < 5:
                print(f"   {t}: n={len(g)} (too thin for CI)")
                continue
            _n, h, r, s = stats(g)
            ci = boot_ci((g["fwd_ret_20"] > 0).astype(float).to_numpy(),
                         (ga["fwd_ret_20"] > 0).astype(float).to_numpy(), rng)
            print(f"   {t}: n={_n} hit={h*100:.1f}% ret={r*100:+.2f}% "
                  f"sharpe={s:+.3f} CI[{ci[0]*100:+.1f},{ci[1]*100:+.1f}]pts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
