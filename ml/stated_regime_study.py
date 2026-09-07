"""ml/stated_regime_study.py — T1: the B(b) regime study re-run with
quad = HEDGEYE'S STATED MONTHLY QUAD as-of the bar (audited
quad_nowcast_daily dial), instead of the realized-FRED quarter.

Identical cells / rules / guards to ml/regime_study.py. Join honesty:
the dial value used for bar D is the dial as of D-1 (the note behind a
same-day dial move can post after the 09:30 feature known_at; previous
close is unambiguous). Questions (a) gold-dip outside the bull and
(b) defensive failure concentration re-asked under stated quads.

    py ml/stated_regime_study.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()
from ml.universe import ASSET_CLASS, TICKERS as ALL_TICKERS  # noqa: E402
from ml.regime_study import (boot_ci, cell_table, subperiod,  # noqa: E402
                             MIN_RP_ROWS, STANDOUTS, DEFENSIVES, OUT,
                             LINES, say)


def load():
    with db_pg.get_conn() as conn:
        f = pd.read_sql(
            "SELECT f.ticker, f.bar_date, f.rp, f.above_trend, "
            "f.decel_streak, t.fwd_ret_20 "
            "FROM ml_features f JOIN ml_targets t USING (ticker, bar_date) "
            "WHERE f.ticker = ANY(%s) AND t.fwd_ret_20 IS NOT NULL "
            "AND f.rp IS NOT NULL", conn, params=(ALL_TICKERS,))
        dial = pd.read_sql(
            "SELECT date, monthly_quad FROM quad_nowcast_daily "
            "WHERE monthly_quad IS NOT NULL ORDER BY date", conn)
        vix = pd.read_sql("SELECT bar_date, c FROM px_daily "
                          "WHERE ticker='^VIX'", conn)
    f["bar_date"] = pd.to_datetime(f["bar_date"])
    for c in ("rp", "above_trend", "decel_streak", "fwd_ret_20"):
        f[c] = f[c].astype(float)
    cov = f.groupby("ticker")["rp"].count()
    f = f[f["ticker"].isin(cov[cov >= MIN_RP_ROWS].index)]
    f["asset_class"] = f["ticker"].map(ASSET_CLASS)

    # stated dial as-of the PREVIOUS trading day (no same-day lookahead)
    dial["date"] = pd.to_datetime(dial["date"])
    f = f.sort_values("bar_date")
    f = pd.merge_asof(f, dial.rename(columns={"date": "bar_date",
                                              "monthly_quad": "quad"}),
                      on="bar_date", direction="backward",
                      allow_exact_matches=False)
    vix["bar_date"] = pd.to_datetime(vix["bar_date"])
    vix["vix_bucket"] = np.where(vix["c"].astype(float) < 20, "<20",
                                 np.where(vix["c"].astype(float) <= 30,
                                          "20-30", ">30"))
    f = f.merge(vix[["bar_date", "vix_bucket"]], on="bar_date", how="left")
    f["lrr"] = (f["above_trend"] == 1) & (f["rp"] < 0.45)
    f["dip"] = f["lrr"] & (f["decel_streak"] >= 1)
    return f.reset_index(drop=True)


def main() -> int:
    rng = np.random.default_rng(11)
    f = load()
    say(f"# T1 — stated-quad regime study ({date.today()})")
    say(f"\nSame cells/rules/guards as Phase B(b), but quad = Hedgeye's "
        f"stated MONTHLY quad as-of the prior bar (quad_nowcast_daily, "
        f"audited method-tier dial). Rows {len(f)} across "
        f"{f['ticker'].nunique()} tickers, "
        f"{f['bar_date'].min().date()} .. {f['bar_date'].max().date()}; "
        f"stated-dial coverage {f['quad'].notna().mean()*100:.1f}% of bars "
        f"(dial exists only where the mail archive reaches — dense from "
        f"2023-04). HYG excluded (held).")
    say("\nStated-quad distribution of bars: "
        + ", ".join(f"Q{int(q)}: {n}" for q, n in
                    f["quad"].value_counts().sort_index().items()))
    counters = {"tested": 0, "positive": 0, "negative": 0}
    for rule in ("lrr", "dip"):
        cell_table(f, rule, ["asset_class", "quad"],
                   "asset class x stated Quad", rng, counters)
        cell_table(f, rule, ["asset_class", "vix_bucket"],
                   "asset class x VIX bucket", rng, counters)
        cell_table(f[f["ticker"].isin(STANDOUTS)], rule, ["ticker", "quad"],
                   "standout ticker x stated Quad", rng, counters)

    say("\n## Question (a) — gold dip outside the gold bull, stated quads")
    gold = f["ticker"].isin(["AAAU", "GLD"])
    subperiod(f, "dip", gold, "gold (AAAU+GLD) setup_dip, all stated quads",
              rng)
    for q in (2, 3):
        subperiod(f, "dip", gold & (f["quad"] == q),
                  f"gold setup_dip, stated Quad {q} only", rng)

    say("\n## Question (b) — defensive-sector LRR failure by stated Quad")
    for t in DEFENSIVES:
        sel = f["ticker"] == t
        say(f"\n{t} setup_lrr by stated Quad:")
        for q in (1, 2, 3, 4):
            cell = f[sel & (f["quad"] == q)]
            r = cell[cell["lrr"]]
            if len(r) < 5:
                say(f"  Q{q}: n={len(r)} thin")
                continue
            rh = (r["fwd_ret_20"] > 0).astype(float).to_numpy()
            ah = (cell["fwd_ret_20"] > 0).astype(float).to_numpy()
            ci = boot_ci(rh, ah, rng)
            ci_s = f"[{ci[0]*100:+.1f},{ci[1]*100:+.1f}]" if ci else "thin"
            say(f"  Q{q}: n={len(r)} hit={rh.mean()*100:.1f}% "
                f"ret={r['fwd_ret_20'].mean()*100:+.2f}% "
                f"(any {ah.mean()*100:.1f}%) CI {ci_s}")

    say("\n## Multiple-testing guard")
    say(f"Cells tested: {counters['tested']}; expected chance positives at "
        f"90% CI ~{counters['tested']*0.05:.1f}. Observed "
        f"{counters['positive']} positive / {counters['negative']} negative "
        f"CIs excluding zero. n<100 cells flagged.")

    p = OUT / f"ml_round2_T1_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
