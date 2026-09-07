"""ml/walkforward.py — Round-2 Phase A Step 4: walk-forward evaluation.

Candidate sets (operator decision, 2026-09-07):
  setup_any  every bar with a defined rp (control)
  setup_lrr  above_trend == 1 AND rp < 0.45   (the process's actual gate —
             PRIMARY; the volume tell stays as FEATURES, not a filter)
  setup_dip  setup_lrr AND decel_streak >= 1  (secondary; also evaluated as
             a flagged subset inside setup_lrr's own model)

Walk-forward only: expanding window by calendar year — train <=2020 ->
test 2021, ... train <=2025 -> test 2026 YTD. Purge = drop each ticker's
last 30 bars from every train window (labels look <=30 bars forward).
Never shuffled.

Models per setup per fold:
  lgbm_reg   LightGBM regressor on fwd_sharpe_20, winsorized at +/-5 IN
             TRAINING ONLY (COVID rv20 tails); all evaluation is against
             the RAW values.
  lgbm_cls   LightGBM classifier on fwd_ret_20 > 0
  logit      logistic regression baseline (median-impute + standardize,
             one-hot ticker)
  random     shuffled-target LightGBM refit per fold — every metric is
             read relative to this band.

Metrics per fold: Spearman IC (prediction vs RAW fwd_sharpe_20), AUC,
top-decile mean fwd_ret_20 + hit rate vs the fold's all-candidate base.
Results land in ml_runs (params/metrics/importance JSON); ml/report.py
renders reports/ml_round2_phaseA_<date>.md + equity-curve PNGs.

    py ml/walkforward.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402

TICKERS = ["SPY", "UUP", "USO", "AAAU", "TLT"]
TEST_YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
PURGE_BARS = 30
WINSOR = 5.0
FEATURES = [
    "rp", "rp_dev20", "bulldist", "rng_width", "ltrp", "hurst64", "hurst256",
    "trend_dist", "trade_dist", "above_trend", "vixfix", "volatility",
    "buy", "mega_buy", "sell", "mega_sell",
    "rp_d3", "bulldist_d3", "hi_d3", "lo_d3", "trend_dist_d3", "hurst64_d5",
    "rng_width_d5", "decel_streak", "distribution",
    "vix_level", "vix_bucket", "vix_roc5", "hyg_roc10", "uup_rp", "uup_rp_d3",
    "corr30_spy", "corr30_uup", "usd_pressure", "rv20",
]
LGBM_PARAMS = dict(n_estimators=400, learning_rate=0.05, num_leaves=31,
                   min_child_samples=5, subsample=0.9, colsample_bytree=0.9,
                   random_state=42, verbose=-1)


def load():
    with db_pg.get_conn() as conn:
        f = pd.read_sql(
            "SELECT f.*, t.fwd_ret_20, t.fwd_sharpe_20, t.rr_hit_5_2p5_30 "
            "FROM ml_features f JOIN ml_targets t USING (ticker, bar_date) "
            "WHERE f.ticker = ANY(%s)", conn, params=(TICKERS,))
    f["bar_date"] = pd.to_datetime(f["bar_date"])
    for c in f.columns:
        if c not in ("ticker", "bar_date", "known_at", "source", "built_at"):
            f[c] = f[c].astype(float)
    f["year"] = f["bar_date"].dt.year
    f = f.sort_values(["ticker", "bar_date"]).reset_index(drop=True)
    return f


def masks(f):
    lrr = (f["above_trend"] == 1) & (f["rp"] < 0.45)
    return {
        "setup_any": f["rp"].notna(),
        "setup_lrr": lrr,
        "setup_dip": lrr & (f["decel_streak"] >= 1),
    }


def split(f, test_year):
    train = f[f["year"] < test_year]
    # purge: drop each ticker's LAST 30 bars from the train window
    keep = []
    for _t, g in train.groupby("ticker"):
        keep.append(g.sort_values("bar_date").iloc[:-PURGE_BARS])
    train = pd.concat(keep) if keep else train.iloc[0:0]
    test = f[f["year"] == test_year]
    return train, test


def xmat(df, for_linear=False):
    X = df[FEATURES].copy()
    if for_linear:
        X = pd.get_dummies(pd.concat([X, df["ticker"]], axis=1),
                           columns=["ticker"], dtype=float)
    else:
        X["ticker"] = df["ticker"].astype("category")
    return X


def decile_stats(pred, test):
    """(top-decile mean fwd_ret_20, hit rate, base mean, base hit, n_top)."""
    ok = np.isfinite(pred) & test["fwd_ret_20"].notna().to_numpy()
    if ok.sum() < 10:
        return (np.nan,) * 4 + (0,)
    r = test.loc[ok, "fwd_ret_20"].to_numpy()
    p = pred[ok]
    k = max(1, len(p) // 10)
    top = r[np.argsort(-p)[:k]]
    return (float(top.mean()), float((top > 0).mean()),
            float(r.mean()), float((r > 0).mean()), int(k))


def fit_fold(tr, te, rng):
    """Returns dict of model->(pred_reg_or_prob, extras)."""
    from lightgbm import LGBMClassifier, LGBMRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    out = {}
    ytr_reg = tr["fwd_sharpe_20"].clip(-WINSOR, WINSOR)
    ytr_cls = (tr["fwd_ret_20"] > 0).astype(int)

    Xtr, Xte = xmat(tr), xmat(te)
    reg = LGBMRegressor(**LGBM_PARAMS)
    reg.fit(Xtr, ytr_reg, categorical_feature=["ticker"])
    out["lgbm_reg"] = reg.predict(Xte)
    gain = dict(zip(Xtr.columns,
                    [float(x) for x in reg.booster_.feature_importance("gain")]))

    cls = LGBMClassifier(**LGBM_PARAMS)
    cls.fit(Xtr, ytr_cls, categorical_feature=["ticker"])
    out["lgbm_cls"] = cls.predict_proba(Xte)[:, 1]

    Xtr_l, Xte_l = xmat(tr, True), xmat(te, True)
    Xte_l = Xte_l.reindex(columns=Xtr_l.columns, fill_value=0.0)
    logit = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(max_iter=2000))
    logit.fit(Xtr_l, ytr_cls)
    out["logit"] = logit.predict_proba(Xte_l)[:, 1]

    rnd = LGBMRegressor(**LGBM_PARAMS)
    rnd.fit(Xtr, rng.permutation(ytr_reg.to_numpy()),
            categorical_feature=["ticker"])
    out["random"] = rnd.predict(Xte)
    return out, gain


def evaluate(pred, te, is_prob=False):
    from sklearn.metrics import roc_auc_score
    m = {}
    ok = np.isfinite(pred) & te["fwd_sharpe_20"].notna().to_numpy()
    if ok.sum() >= 10:
        ic = spearmanr(pred[ok], te.loc[ok, "fwd_sharpe_20"])[0]
        m["ic"] = None if ic != ic else float(ic)
    else:
        m["ic"] = None
    y = (te["fwd_ret_20"] > 0).astype(int)
    okc = np.isfinite(pred) & te["fwd_ret_20"].notna().to_numpy()
    if okc.sum() >= 10 and y[okc].nunique() == 2:
        m["auc"] = float(roc_auc_score(y[okc], pred[okc]))
    else:
        m["auc"] = None
    top_mean, top_hit, base_mean, base_hit, k = decile_stats(pred, te)
    m.update(top_mean=None if top_mean != top_mean else top_mean,
             top_hit=None if top_hit != top_hit else top_hit,
             base_mean=None if base_mean != base_mean else base_mean,
             base_hit=None if base_hit != base_hit else base_hit, n_top=k,
             n_test=int(okc.sum()))
    return m


def main() -> int:
    f = load()
    mk = masks(f)
    run_id = "phaseA_" + datetime.now().strftime("%Y%m%d_%H%M")
    folds, importance, curves, preds_store = [], {}, {}, {}

    print("setup_lrr candidates by ticker x year:")
    d = f[mk["setup_lrr"]].copy()
    tab = d.pivot_table(index="ticker", columns="year", values="bar_date",
                        aggfunc="count").fillna(0).astype(int)
    tab["total"] = tab.sum(axis=1)
    print(tab.to_string())
    print(f"setup_dip total: {int(mk['setup_dip'].sum())}  "
          f"setup_any: {int(mk['setup_any'].sum())}")

    for setup, mask in mk.items():
        sub = f[mask & f["fwd_sharpe_20"].notna() & f["fwd_ret_20"].notna()]
        all_te = []
        for i, ty in enumerate(TEST_YEARS):
            tr, te = split(sub, ty)
            if len(tr) < 100 or len(te) < 10:
                folds.append({"setup": setup, "year": ty, "skipped": True,
                              "n_train": len(tr), "n_test": len(te)})
                continue
            rng = np.random.default_rng(42 + i)
            preds, gain = fit_fold(tr, te, rng)
            importance.setdefault(setup, {})[str(ty)] = gain
            for model, p in preds.items():
                m = evaluate(p, te)
                m.update(setup=setup, year=ty, model=model,
                         n_train=len(tr), n_test=len(te))
                folds.append(m)
            te_keep = te[["ticker", "bar_date", "fwd_ret_20",
                          "fwd_sharpe_20", "decel_streak"]].copy()
            te_keep["pred"] = preds["lgbm_reg"]
            te_keep["pred_rnd"] = preds["random"]
            te_keep["test_year"] = ty
            all_te.append(te_keep)
        if all_te:
            preds_store[setup] = pd.concat(all_te).sort_values("bar_date")

    # equity curves (naive sequential compounding of the 20-bar trade
    # returns, equal weight, per-year decile membership) + per-ticker stats
    summaries = {}
    for setup, dfp in preds_store.items():
        sel = []
        for ty, g in dfp.groupby("test_year"):
            k = max(1, len(g) // 10)
            sel.append(g.nlargest(k, "pred"))
        top = pd.concat(sel).sort_values("bar_date")
        curves[setup] = {
            "dates_top": [d.strftime("%Y-%m-%d") for d in top["bar_date"]],
            "equity_top": (1 + top["fwd_ret_20"]).cumprod().round(4).tolist(),
            "dates_all": [d.strftime("%Y-%m-%d") for d in dfp["bar_date"]],
            "equity_all": (1 + dfp["fwd_ret_20"]).cumprod().round(4).tolist(),
        }
        per_ticker = {}
        for t, g in dfp.groupby("ticker"):
            ic = spearmanr(g["pred"], g["fwd_sharpe_20"])[0] if len(g) >= 10 else np.nan
            per_ticker[t] = {"n": len(g),
                             "ic": None if ic != ic else round(float(ic), 4)}
        summaries[setup] = {"per_ticker": per_ticker}

    # dip-as-subset inside the LRR model's own predictions
    if "setup_lrr" in preds_store:
        dfp = preds_store["setup_lrr"]
        dip_rows = dfp[dfp["decel_streak"] >= 1]
        summaries["dip_subset_of_lrr"] = {
            "n": len(dip_rows),
            "mean_fwd_ret_20": round(float(dip_rows["fwd_ret_20"].mean()), 5),
            "hit": round(float((dip_rows["fwd_ret_20"] > 0).mean()), 4),
            "lrr_all_mean": round(float(dfp["fwd_ret_20"].mean()), 5),
            "lrr_all_hit": round(float((dfp["fwd_ret_20"] > 0).mean()), 4),
        }

    # pass/fail per the desk rule, applied to lrr and dip
    passfail = {}
    for setup in ("setup_lrr", "setup_dip"):
        rows = [x for x in folds if x.get("setup") == setup
                and x.get("model") == "lgbm_reg" and not x.get("skipped")]
        rnds = [x for x in folds if x.get("setup") == setup
                and x.get("model") == "random" and not x.get("skipped")]
        def _agg(rs):
            ics = [x["ic"] for x in rs if x["ic"] is not None]
            pos_years = sum(1 for x in ics if x > 0)
            th = [x for x in rs if x["top_hit"] is not None]
            hit_edge = (np.mean([x["top_hit"] for x in th])
                        - np.mean([x["base_hit"] for x in th])) if th else np.nan
            return pos_years, len(ics), hit_edge
        py_m, n_m, edge_m = _agg(rows)
        py_r, n_r, edge_r = _agg(rnds)
        model_pass = (edge_m == edge_m and edge_m >= 0.05 and py_m >= 4)
        random_pass = (edge_r == edge_r and edge_r >= 0.05 and py_r >= 4)
        passfail[setup] = {
            "model": {"ic_pos_years": f"{py_m}/{n_m}",
                      "hit_edge_pts": None if edge_m != edge_m else round(edge_m * 100, 2),
                      "passes": bool(model_pass)},
            "random": {"ic_pos_years": f"{py_r}/{n_r}",
                       "hit_edge_pts": None if edge_r != edge_r else round(edge_r * 100, 2),
                       "passes": bool(random_pass)},
            "verdict": "PASS" if (model_pass and not random_pass) else "FAIL",
        }

    metrics = {"folds": folds, "summaries": summaries, "passfail": passfail,
               "curves": curves,
               "lrr_counts": {str(k): {str(y): int(v) for y, v in r.items()}
                              for k, r in tab.iterrows()}}
    params = {"features": FEATURES, "lgbm": LGBM_PARAMS, "winsor": WINSOR,
              "purge_bars": PURGE_BARS, "test_years": TEST_YEARS,
              "setups": {k: int(v.sum()) for k, v in mk.items()}}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO ml_runs (run_id, params, metrics, importance) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO UPDATE "
                    "SET params=EXCLUDED.params, metrics=EXCLUDED.metrics, "
                    "importance=EXCLUDED.importance",
                    (run_id, json.dumps(params), json.dumps(metrics),
                     json.dumps(importance)))
        conn.commit()
    print(f"\nrun {run_id} stored ({len(folds)} fold rows)")
    for s, pf in passfail.items():
        print(f"  {s}: {pf['verdict']}  model {pf['model']}  random {pf['random']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
