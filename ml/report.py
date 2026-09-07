"""ml/report.py — Round-2 Phase A Step 5: render the report from ml_runs.

Reads the latest (or --run-id) ml_runs row and writes
reports/ml_round2_phaseA_<date>.md plus equity-curve PNGs. The report is
regenerable from the corpus — nothing in it is hand-computed.

    py ml/report.py [--run-id phaseA_...]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402

OUT = REPO / "reports"


def fmt(x, pct=False, digits=3):
    if x is None:
        return "—"
    return f"{x*100:.1f}%" if pct else f"{x:.{digits}f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id")
    args = ap.parse_args()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        if args.run_id:
            cur.execute("SELECT run_id, params, metrics, importance FROM ml_runs "
                        "WHERE run_id=%s", (args.run_id,))
        else:
            cur.execute("SELECT run_id, params, metrics, importance FROM ml_runs "
                        "ORDER BY created_at DESC LIMIT 1")
        run_id, params, metrics, importance = cur.fetchone()

    OUT.mkdir(exist_ok=True)
    stamp = date.today().isoformat()

    # equity-curve PNGs
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pngs = {}
    for setup, c in metrics["curves"].items():
        fig, ax = plt.subplots(figsize=(9, 4.5))
        ax.plot(range(len(c["equity_all"])), c["equity_all"],
                label=f"all candidates (n={len(c['equity_all'])})", lw=1)
        ax.plot([c["dates_all"].index(d) if d in c["dates_all"] else None
                 for d in c["dates_top"]],
                c["equity_top"], label=f"top decile (n={len(c['equity_top'])})", lw=1.6)
        ax.set_title(f"{setup}: naive sequential equity, 20-bar trades, equal weight")
        ax.set_ylabel("growth of 1"), ax.legend(), ax.grid(alpha=0.3)
        p = OUT / f"ml_round2_phaseA_{stamp}_{setup}.png"
        fig.tight_layout(), fig.savefig(p, dpi=110), plt.close(fig)
        pngs[setup] = p.name

    L = []
    A = L.append
    A(f"# ML Round 2 — Phase A walk-forward report ({stamp}, run `{run_id}`)")
    A("")
    A("Context: TrendSpider's ML Quant Lab Round 1 ended 24 models / 0 passes "
      "(docs/TRENDSPIDER_ML_ROUND1_RESULTS.md); its two usable lessons — MFR "
      "features out-rank RSI with TREND-line distance #1, and the fixed "
      "TP-before-SL target is the wrong question — set this phase's design. "
      "Regime data (FRED Quad, CBOE vol) is Phase B "
      "(docs/ROUND2_DATA_ROADMAP.md).")
    A("")
    A("**What the model is for**: a RANKER of rule-generated candidates — the "
      "process rules decide what is even considerable; the model orders those "
      "bars by expected regime-normalized forward return (`fwd_sharpe_20`). "
      "It is not a signal generator.")
    A("")
    A("Method notes: expanding yearly walk-forward (test 2021–2026 YTD), "
      f"{params['purge_bars']}-bar purge per ticker at every boundary, never "
      "shuffled. Training target winsorized at ±5 (COVID-era rv20 tails); "
      "ALL evaluation is against raw values. `rp` here is bar D's own close "
      "vs range D at 16:00 ET — distinct from the exported `#MFR_*_RP` "
      "(prior close, 09:30); see ml/README.md. The shuffled-target LightGBM "
      "is refit per fold; read every number relative to it.")
    A("")
    A("## Candidate sets")
    A("")
    A("| set | definition | rows |")
    A("|---|---|---|")
    A(f"| setup_any | every bar with a defined rp (control) | {params['setups']['setup_any']} |")
    A(f"| setup_lrr | above_trend==1 AND rp<0.45 (primary) | {params['setups']['setup_lrr']} |")
    A(f"| setup_dip | setup_lrr AND decel_streak>=1 | {params['setups']['setup_dip']} |")
    A("")
    A("setup_lrr by ticker × year:")
    A("")
    years = sorted({y for r in metrics["lrr_counts"].values() for y in r})
    A("| ticker | " + " | ".join(years) + " |")
    A("|---" * (len(years) + 1) + "|")
    for t, r in metrics["lrr_counts"].items():
        A(f"| {t} | " + " | ".join(str(r.get(y, 0)) for y in years) + " |")
    A("")
    A("## Fold metrics (per test year)")
    for setup in ("setup_any", "setup_lrr", "setup_dip"):
        A("")
        A(f"### {setup}")
        A("")
        A("| year | model | n_test | IC | AUC | top-dec mean | top-dec hit | base mean | base hit |")
        A("|---|---|---|---|---|---|---|---|---|")
        for x in metrics["folds"]:
            if x.get("setup") != setup:
                continue
            if x.get("skipped"):
                A(f"| {x['year']} | — | {x['n_test']} | skipped (n_train={x['n_train']}) | | | | | |")
                continue
            A(f"| {x['year']} | {x['model']} | {x['n_test']} | {fmt(x['ic'])} | "
              f"{fmt(x['auc'])} | {fmt(x['top_mean'], True)} | "
              f"{fmt(x['top_hit'], True)} | {fmt(x['base_mean'], True)} | "
              f"{fmt(x['base_hit'], True)} |")
    A("")
    A("## Pass/fail (desk rule: top decile ≥ +5 pts hit edge AND IC>0 in ≥4 of "
      "6 test years, random must fail)")
    A("")
    for s, pf in metrics["passfail"].items():
        A(f"- **{s}: {pf['verdict']}** — model hit edge "
          f"{pf['model']['hit_edge_pts']} pts, IC>0 {pf['model']['ic_pos_years']}; "
          f"random {pf['random']['hit_edge_pts']} pts, "
          f"IC>0 {pf['random']['ic_pos_years']}")
    A("")
    A("## Dip flag as a SUBSET of the LRR model")
    d = metrics["summaries"].get("dip_subset_of_lrr", {})
    if d:
        A("")
        A(f"Inside setup_lrr's own out-of-sample predictions, rows with "
          f"decel_streak>=1 (n={d['n']}): mean fwd_ret_20 "
          f"{d['mean_fwd_ret_20']*100:.2f}% / hit {d['hit']*100:.1f}% vs all "
          f"LRR candidates {d['lrr_all_mean']*100:.2f}% / "
          f"{d['lrr_all_hit']*100:.1f}%.")
    A("")
    A("## Per-ticker IC (lgbm_reg, pooled test years)")
    A("")
    for setup, s in metrics["summaries"].items():
        if "per_ticker" not in s:
            continue
        A(f"- {setup}: " + ", ".join(
            f"{t} {v['ic'] if v['ic'] is not None else '—'} (n={v['n']})"
            for t, v in sorted(s["per_ticker"].items())))
    A("")
    A("## Feature importance (LightGBM gain, top 10 of the final fold)")
    A("")
    for setup, by_year in importance.items():
        last = by_year[max(by_year)]
        top = sorted(last.items(), key=lambda kv: -kv[1])[:10]
        A(f"- {setup} ({max(by_year)}): " + ", ".join(
            f"{k} {v:.0f}" for k, v in top))
    A("")
    A("## Equity curves")
    A("")
    A("Naive sequential compounding of each selected 20-bar trade (equal "
      "weight, overlaps ignored) — comparative shape only, not a backtest.")
    for setup, name in pngs.items():
        A(f"![{setup}]({name})")
    A("")
    A("## Standing notes")
    A("- `mega_buy` fired 0/88 within ±3 bars of the old strict dip rule: the "
      "indicator's buy logic is a different animal (capitulation/breakout). "
      "It stays as a feature; no reconciliation attempted.")
    A("- `rr_hit_5_2p5_30` is the Round-1 continuity check only. UUP's 4.0% "
      "base rate is the Round-1 lesson (fixed 5% TP unreachable inside "
      "dollar-ETF ranges); no conclusions drawn from it.")
    A("- TLT rows are `tv_unverified` (indicator-only, the 2026-09-06 waiver).")

    p = OUT / f"ml_round2_phaseA_{stamp}.md"
    p.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {p} + {len(pngs)} PNG(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
