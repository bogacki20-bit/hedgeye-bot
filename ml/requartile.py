"""ml/requartile.py — re-score the setup_dip ranker at TOP-QUARTILE
(operator ask 2026-09-07): same folds, same seeds as walkforward.py
(rng 42+fold-index, so the shuffled baseline is bit-identical), metrics at
k = n//4 instead of n//10, plus the 2024 fold's equity curves for model vs
shuffled baseline (the year random beat the model at decile size).

    py ml/requartile.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

from ml.walkforward import (LGBM_PARAMS, TEST_YEARS, WINSOR, load,  # noqa: E402
                            masks, split, xmat)

OUT = REPO / "reports"


def main() -> int:
    from lightgbm import LGBMRegressor
    f = load()
    m = masks(f)["setup_dip"]
    sub = f[m & f["fwd_sharpe_20"].notna() & f["fwd_ret_20"].notna()]
    print("setup_dip @ TOP-QUARTILE (same folds/seeds as run phaseA):")
    print(f"{'year':>5} {'n':>4} {'k':>3}  {'model: mean/hit':>18}  "
          f"{'random: mean/hit':>18}  {'base: mean/hit':>16}  IC(m)  IC(r)")
    y2024 = {}
    for i, ty in enumerate(TEST_YEARS):
        tr, te = split(sub, ty)
        if len(tr) < 100 or len(te) < 10:
            print(f"{ty:>5} {len(te):>4}  skipped (n_train={len(tr)})")
            continue
        rng = np.random.default_rng(42 + i)
        ytr = tr["fwd_sharpe_20"].clip(-WINSOR, WINSOR)
        Xtr, Xte = xmat(tr), xmat(te)
        reg = LGBMRegressor(**LGBM_PARAMS)
        reg.fit(Xtr, ytr, categorical_feature=["ticker"])
        pm = reg.predict(Xte)
        rnd = LGBMRegressor(**LGBM_PARAMS)
        rnd.fit(Xtr, rng.permutation(ytr.to_numpy()),
                categorical_feature=["ticker"])
        pr = rnd.predict(Xte)
        r = te["fwd_ret_20"].to_numpy()
        k = max(1, len(r) // 4)
        top_m = r[np.argsort(-pm)[:k]]
        top_r = r[np.argsort(-pr)[:k]]
        ic_m = spearmanr(pm, te["fwd_sharpe_20"])[0]
        ic_r = spearmanr(pr, te["fwd_sharpe_20"])[0]
        print(f"{ty:>5} {len(r):>4} {k:>3}  "
              f"{top_m.mean()*100:+6.2f}% /{(top_m>0).mean()*100:5.1f}%   "
              f"{top_r.mean()*100:+6.2f}% /{(top_r>0).mean()*100:5.1f}%   "
              f"{r.mean()*100:+6.2f}% /{(r>0).mean()*100:5.1f}%  "
              f"{ic_m:+.3f} {ic_r:+.3f}")
        if ty == 2024:
            order_m = np.argsort(-pm)[:k]
            order_r = np.argsort(-pr)[:k]
            dts = te["bar_date"].to_numpy()
            y2024 = {
                "model": sorted(zip(dts[order_m], r[order_m])),
                "random": sorted(zip(dts[order_r], r[order_r])),
            }
    if y2024:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 4.5))
        for name, series in y2024.items():
            eq = np.cumprod([1 + x for _, x in series])
            ax.plot([d for d, _ in series], eq, marker="o", ms=3,
                    label=f"{name} top-quartile (n={len(series)})")
        ax.set_title("setup_dip 2024 test fold: model vs shuffled-target, "
                     "top-quartile picks (naive sequential compounding)")
        ax.grid(alpha=0.3), ax.legend(), ax.set_ylabel("growth of 1")
        fig.autofmt_xdate(), fig.tight_layout()
        p = OUT / "ml_round2_phaseA_2024_dip_quartile_model_vs_random.png"
        fig.savefig(p, dpi=110), plt.close(fig)
        print(f"\n2024 curves -> {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
