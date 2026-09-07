"""ml/vol_regime_study.py — Phase B(c): the commodity-dip cell stress-test.

Commodity class only (USO, GLD, AAAU). Rules lrr/dip vs commodity
setup_any WITHIN THE SAME CELL, bootstrap 90% CI of the hit-diff, across
three conditionings:
  1. VIX band            <20 / 20-30 / >30   (B(b) comparability)
  2. VIX/VIX3M regime    <0.9 deep contango / 0.9-1.0 / >1.0 backwardation
  3. the asset's OWN vol index band — OVX for USO, GVZ for GLD+AAAU:
       OVX <33 / 33-42 / >42 ; GVZ <15 / 15-18 / >18
     (round numbers at the 2018-2026 terciles; stated, in-sample bands.)
Plus the cross: dip x own-vol-mid split by TS regime — is the B(b) edge a
mid-vol phenomenon or a backwardation phenomenon, on the asset's own vol?

    py ml/vol_regime_study.py
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

NBOOT = 10_000
COMMODITIES = {"USO": "^OVX", "GLD": "^GVZ", "AAAU": "^GVZ"}
OUT = REPO / "reports"
LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


def load():
    with db_pg.get_conn() as conn:
        f = pd.read_sql(
            "SELECT f.ticker, f.bar_date, f.rp, f.above_trend, "
            "f.decel_streak, t.fwd_ret_20 "
            "FROM ml_features f JOIN ml_targets t USING (ticker, bar_date) "
            "WHERE f.ticker = ANY(%s) AND t.fwd_ret_20 IS NOT NULL "
            "AND f.rp IS NOT NULL", conn, params=(list(COMMODITIES),))
        px = pd.read_sql(
            "SELECT ticker, bar_date, c FROM px_daily WHERE ticker IN "
            "('^VIX','^VIX3M','^OVX','^GVZ')", conn)
    f["bar_date"] = pd.to_datetime(f["bar_date"])
    for c in ("rp", "above_trend", "decel_streak", "fwd_ret_20"):
        f[c] = f[c].astype(float)
    px["bar_date"] = pd.to_datetime(px["bar_date"])
    v = px.pivot(index="bar_date", columns="ticker", values="c").astype(float)

    f["vix"] = v["^VIX"].reindex(f["bar_date"]).to_numpy()
    f["ts"] = (v["^VIX"] / v["^VIX3M"]).reindex(f["bar_date"]).to_numpy()
    own = np.where(f["ticker"] == "USO",
                   v["^OVX"].reindex(f["bar_date"]).to_numpy(),
                   v["^GVZ"].reindex(f["bar_date"]).to_numpy())
    f["own_vol"] = own

    f["vix_band"] = pd.cut(f["vix"], [0, 20, 30, np.inf],
                           labels=["<20", "20-30", ">30"])
    f["ts_band"] = pd.cut(f["ts"], [0, 0.9, 1.0, np.inf],
                          labels=["<0.9 contango", "0.9-1.0", ">1.0 backwd"])
    lo = np.where(f["ticker"] == "USO", 33, 15)
    hi = np.where(f["ticker"] == "USO", 42, 18)
    f["own_band"] = np.select(
        [f["own_vol"] < lo, f["own_vol"] <= hi, f["own_vol"] > hi],
        ["low", "mid", "high"], default=None)

    f["lrr"] = (f["above_trend"] == 1) & (f["rp"] < 0.45)
    f["dip"] = f["lrr"] & (f["decel_streak"] >= 1)
    return f.reset_index(drop=True)


def boot_ci(rh, ah, rng):
    if len(rh) < 20 or len(ah) < 20:
        return None
    d = (rng.choice(rh, (NBOOT, len(rh))).mean(axis=1)
         - rng.choice(ah, (NBOOT, len(ah))).mean(axis=1))
    return float(np.percentile(d, 5)), float(np.percentile(d, 95))


def table(f, rule, band_col, label, rng, counters):
    say(f"\n### commodity {rule} x {label}")
    say(f"{'band':<16} {'n':>5} {'hit':>6} {'ret20':>7} "
        f"{'anyN':>6} {'anyHit':>6}  hit-diff 90% CI")
    for band, cell in f.groupby(band_col, observed=True, dropna=True):
        r = cell[cell[rule]]
        if len(r) == 0:
            continue
        rh = (r["fwd_ret_20"] > 0).astype(float).to_numpy()
        ah = (cell["fwd_ret_20"] > 0).astype(float).to_numpy()
        ci = boot_ci(rh, ah, rng)
        counters["tested"] += 1
        if ci and ci[0] > 0:
            counters["pos"] += 1
        if ci and ci[1] < 0:
            counters["neg"] += 1
        ci_s = f"[{ci[0]*100:+5.1f},{ci[1]*100:+5.1f}]" if ci else "  thin  "
        star = " **" if ci and (ci[0] > 0 or ci[1] < 0) else ""
        flag = " (n<100)" if len(r) < 100 else ""
        say(f"{str(band):<16} {len(r):>5} {rh.mean()*100:5.1f}% "
            f"{r['fwd_ret_20'].mean()*100:+6.2f}% {len(cell):>6} "
            f"{ah.mean()*100:5.1f}%  {ci_s}{star}{flag}")


def main() -> int:
    rng = np.random.default_rng(23)
    f = load()
    say(f"# Phase B(c) — commodity-dip vs vol regimes ({date.today()})")
    say(f"\nCommodity bars (USO/GLD/AAAU) with targets: {len(f)}, "
        f"{f['bar_date'].min().date()} .. {f['bar_date'].max().date()}. "
        f"Own-vol joins: OVX for USO, GVZ for GLD/AAAU. Bands are stated "
        f"in-sample terciles. Baseline = commodity setup_any in the SAME "
        f"cell. Rules study only — the ranker stays retired.")
    say(f"Coverage: vix {f['vix'].notna().mean()*100:.0f}% · "
        f"ts {f['ts'].notna().mean()*100:.0f}% · "
        f"own_vol {f['own_vol'].notna().mean()*100:.0f}% of bars. "
        f"TS distribution: contango<0.9 "
        f"{(f['ts'] < 0.9).mean()*100:.0f}% · 0.9-1.0 "
        f"{((f['ts'] >= 0.9) & (f['ts'] <= 1)).mean()*100:.0f}% · "
        f"backwardation>1 {(f['ts'] > 1).mean()*100:.0f}%")
    counters = {"tested": 0, "pos": 0, "neg": 0}
    for rule in ("lrr", "dip"):
        table(f, rule, "vix_band", "VIX band", rng, counters)
        table(f, rule, "ts_band", "VIX/VIX3M term structure", rng, counters)
        table(f, rule, "own_band", "OWN vol index band (OVX/GVZ)", rng, counters)

    say("\n### the cross: dip x own-vol MID, split by term structure")
    mid = f[(f["own_band"] == "mid")]
    for ts_band, cell in mid.groupby("ts_band", observed=True):
        r = cell[cell["dip"]]
        rh = (r["fwd_ret_20"] > 0).astype(float).to_numpy()
        ah = (cell["fwd_ret_20"] > 0).astype(float).to_numpy()
        ci = boot_ci(rh, ah, rng)
        ci_s = f"[{ci[0]*100:+.1f},{ci[1]*100:+.1f}]" if ci else "thin"
        say(f"  own-mid & {ts_band}: n={len(r)} "
            f"hit={rh.mean()*100 if len(r) else float('nan'):.1f}% "
            f"ret={r['fwd_ret_20'].mean()*100 if len(r) else float('nan'):+.2f}% "
            f"(any {ah.mean()*100:.1f}%) CI {ci_s}")

    say("\n### multiple-testing guard")
    say(f"Cells tested: {counters['tested']}; expected chance positives at "
        f"90% ~{counters['tested']*0.05:.1f}. Observed {counters['pos']} "
        f"positive / {counters['neg']} negative CIs excluding zero. "
        f"n<100 cells flagged.")
    p = OUT / f"ml_round2_phaseBc_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
