"""ml/regime_study.py — Phase B(b): regime-conditioned RULE study (no model;
the ranker is retired after two inverted OOS rounds).

For setup_lrr / setup_dip vs setup_any WITHIN THE SAME REGIME CELL:
mean fwd_ret_20, hit rate, n, bootstrap 90% CI of the hit-rate difference.
Cells: asset_class x Quad, asset_class x VIX bucket, ticker x Quad for the
standouts (AAAU GLD XLY XLU USO). Quad joins as-of known_at (realized
quarter, usable only from ~its GDP advance release — a 2-4 month lag by
construction). VIX bucket from px_daily ^VIX close: <20 / 20-30 / >30.

Full period 2018-2026 (history now starts 2018); the question-4 cells are
also split 2018-2020 vs 2021-2026. Multiple-testing guard: cells tested,
expected chance positives at 90% (5% upper tail), n<100 flags.

    py ml/regime_study.py            # prints tables + writes the report
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
from ml.universe import ASSET_CLASS, TICKERS as ALL_TICKERS  # noqa: E402

NBOOT = 10_000
MIN_RP_ROWS = 500
STANDOUTS = ["AAAU", "GLD", "XLY", "XLU", "USO"]
DEFENSIVES = ["XLU", "XLP", "XLRE"]
OUT = REPO / "reports"
LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


def load():
    with db_pg.get_conn() as conn:
        f = pd.read_sql(
            "SELECT f.ticker, f.bar_date, f.known_at, f.rp, f.above_trend, "
            "f.decel_streak, t.fwd_ret_20 "
            "FROM ml_features f JOIN ml_targets t USING (ticker, bar_date) "
            "WHERE f.ticker = ANY(%s) AND t.fwd_ret_20 IS NOT NULL "
            "AND f.rp IS NOT NULL", conn, params=(ALL_TICKERS,))
        quad = pd.read_sql("SELECT month, quad, known_at FROM quad_monthly "
                           "ORDER BY known_at", conn)
        vix = pd.read_sql("SELECT bar_date, c FROM px_daily "
                          "WHERE ticker='^VIX'", conn)
    f["bar_date"] = pd.to_datetime(f["bar_date"])
    for c in ("rp", "above_trend", "decel_streak", "fwd_ret_20"):
        f[c] = f[c].astype(float)
    cov = f.groupby("ticker")["rp"].count()
    f = f[f["ticker"].isin(cov[cov >= MIN_RP_ROWS].index)]
    f["asset_class"] = f["ticker"].map(ASSET_CLASS)

    # Quad as-of join: latest quad whose known_at <= the bar's known_at
    quad["known_at"] = pd.to_datetime(quad["known_at"], utc=True)
    f["known_at"] = pd.to_datetime(f["known_at"], utc=True)
    f = f.sort_values("known_at")
    f = pd.merge_asof(f, quad[["known_at", "quad"]].sort_values("known_at"),
                      on="known_at", direction="backward")
    vix["bar_date"] = pd.to_datetime(vix["bar_date"])
    vix["vix_bucket"] = np.where(vix["c"].astype(float) < 20, "<20",
                                 np.where(vix["c"].astype(float) <= 30,
                                          "20-30", ">30"))
    f = f.merge(vix[["bar_date", "vix_bucket"]], on="bar_date", how="left")
    f["lrr"] = (f["above_trend"] == 1) & (f["rp"] < 0.45)
    f["dip"] = f["lrr"] & (f["decel_streak"] >= 1)
    return f.reset_index(drop=True)


def boot_ci(rule_hits, any_hits, rng):
    if len(rule_hits) < 20 or len(any_hits) < 20:
        return None
    d = (rng.choice(rule_hits, (NBOOT, len(rule_hits))).mean(axis=1)
         - rng.choice(any_hits, (NBOOT, len(any_hits))).mean(axis=1))
    return float(np.percentile(d, 5)), float(np.percentile(d, 95))


def cell_table(f, rule_col, group_cols, label, rng, counters):
    say(f"\n### {label} — {rule_col} vs any (same cell)")
    say(f"{'cell':<24} {'n':>5} {'hit':>6} {'ret20':>7} "
        f"{'anyN':>6} {'anyHit':>6}  hit-diff 90% CI")
    for keys, cell in f.groupby(group_cols, dropna=True, observed=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        name = "/".join(str(k) for k in keys)
        r = cell[cell[rule_col]]
        if len(r) == 0:
            continue
        rh = (r["fwd_ret_20"] > 0).astype(float).to_numpy()
        ah = (cell["fwd_ret_20"] > 0).astype(float).to_numpy()
        ci = boot_ci(rh, ah, rng)
        flag = " (n<100)" if len(r) < 100 else ""
        counters["tested"] += 1
        if ci and ci[0] > 0:
            counters["positive"] += 1
        if ci and ci[1] < 0:
            counters["negative"] += 1
        ci_s = (f"[{ci[0]*100:+5.1f},{ci[1]*100:+5.1f}]" if ci else "  thin  ")
        star = " **" if ci and (ci[0] > 0 or ci[1] < 0) else ""
        say(f"{name:<24} {len(r):>5} {rh.mean()*100:5.1f}% "
            f"{r['fwd_ret_20'].mean()*100:+6.2f}% {len(cell):>6} "
            f"{ah.mean()*100:5.1f}%  {ci_s}{star}{flag}")


def subperiod(f, mask_col, sel, label, rng):
    say(f"\n### {label} — 2018-2020 vs 2021-2026")
    for pname, pmask in (("2018-2020", f["bar_date"] < "2021-01-01"),
                         ("2021-2026", f["bar_date"] >= "2021-01-01")):
        cell = f[pmask & sel]
        r = cell[cell[mask_col]]
        if len(r) < 5:
            say(f"  {pname}: n={len(r)} (too thin)")
            continue
        rh = (r["fwd_ret_20"] > 0).astype(float).to_numpy()
        ah = (cell["fwd_ret_20"] > 0).astype(float).to_numpy()
        ci = boot_ci(rh, ah, rng)
        ci_s = f"[{ci[0]*100:+.1f},{ci[1]*100:+.1f}]pts" if ci else "thin"
        say(f"  {pname}: n={len(r)} hit={rh.mean()*100:.1f}% "
            f"ret={r['fwd_ret_20'].mean()*100:+.2f}% "
            f"(any {ah.mean()*100:.1f}%) CI {ci_s}")


def main() -> int:
    rng = np.random.default_rng(11)
    f = load()
    say(f"# Phase B(b) regime-conditioned rule study ({date.today()})")
    say(f"\nRows {len(f)} across {f['ticker'].nunique()} tickers, "
        f"{f['bar_date'].min().date()} .. {f['bar_date'].max().date()}; "
        f"quad coverage {f['quad'].notna().mean()*100:.1f}% of bars "
        f"(realized quarter, known only from ~its GDP advance release). "
        f"HYG excluded (held). Ranker retired; this is a RULE study.")
    say(f"\nQuad distribution of bars: "
        + ", ".join(f"Q{int(q)}: {n}" for q, n in
                    f["quad"].value_counts().sort_index().items()))
    counters = {"tested": 0, "positive": 0, "negative": 0}
    for rule in ("lrr", "dip"):
        cell_table(f, rule, ["asset_class", "quad"],
                   f"asset class x Quad", rng, counters)
        cell_table(f, rule, ["asset_class", "vix_bucket"],
                   f"asset class x VIX bucket", rng, counters)
        cell_table(f[f["ticker"].isin(STANDOUTS)], rule, ["ticker", "quad"],
                   f"standout ticker x Quad", rng, counters)

    say("\n## Question 4a — gold dip outside the gold bull")
    gold = f["ticker"].isin(["AAAU", "GLD"])
    subperiod(f, "dip", gold, "gold (AAAU+GLD) setup_dip, all quads", rng)
    for q in (2, 3):
        subperiod(f, "dip", gold & (f["quad"] == q),
                  f"gold setup_dip, Quad {q} only", rng)

    say("\n## Question 4b — defensive-sector LRR failure by Quad")
    for t in DEFENSIVES:
        sel = f["ticker"] == t
        say(f"\n{t} setup_lrr by Quad:")
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
    say(f"Cells tested (rule x cell, CI computed): {counters['tested']}. "
        f"At a 90% two-sided CI, ~5% clear zero upward by chance -> expected "
        f"~{counters['tested']*0.05:.1f} false positives. Observed: "
        f"{counters['positive']} positive, {counters['negative']} negative "
        f"CIs excluding zero. Cells with n<100 are flagged and should not be "
        f"read alone.")

    p = OUT / f"ml_round2_phaseB_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
