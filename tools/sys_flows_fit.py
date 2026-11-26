"""tools/sys_flows_fit.py - what CTAs must be holding, solved from their own daily returns.

No lookback assumption. Three pieces:

1. POSITIONS FROM RETURNS (fit)
   Take the average daily return of a dozen managed-futures funds (ETFs and mutual funds, all
   priced at the 4 PM close) and find the mix of market exposures that best explains it over the
   last WINDOW days. Markets are ETFs priced at the same 4 PM close. This is how DBMF itself
   copies the CTA index. Output: implied exposure per market as % of fund, and how much of the
   funds' daily moves the mix explains (r2). The same fit run on DBMF alone is checked against
   DBMF's printed book, so we can see how far to trust it.

2. WHICH LOOKBACKS THEY REALLY USE (lookback weights)
   Build a plain trend strategy for each lookback (10, 21, 63, 126, 252 bars), then find the
   non-negative blend that best matches the funds' real returns over 5 years. The blend replaces
   our equal-weight guess in the trend score. Saved to data/sys_flows/lookback_weights.json.

3. DOES PRICE FOLLOW THROUGH AFTER A FLIP (study)
   20+ years of daily bars per market. Event = the close crosses a lookback's reference level.
   Measure the next 5 and 10 bars in the direction of the flip, in units of that market's daily
   vol so markets can be pooled. If it is not above zero, "they have to buy" is not worth much.
   Saved to data/sys_flows/follow_through.json.

Usage: py -m tools.sys_flows_fit --weights --study     (slow parts; rerun monthly)
The daily fit is called from tools.sys_flows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

from tools.sys_flows_markets import CLASSES, MARKETS

FUND_POOL = ["DBMF", "KMLM", "CTA", "WTMF", "AQMIX", "PQTIX", "ASFYX", "AMFAX", "ABYIX", "EBSIX", "MFTFX", "AHLPX"]
WINDOW = 40
RIDGE = 0.10
STUDY_LOOKBACKS = (10, 21, 63, 126, 252)
WEIGHTS_PATH = "data/sys_flows/lookback_weights.json"
STUDY_PATH = "data/sys_flows/follow_through.json"
FACTORS = [(m["key"], m["etf"]) for m in MARKETS if m.get("etf")]


def _download(symbols, period, adjust=True):
    import pandas as pd
    import yfinance as yf
    px = yf.download(sorted(set(symbols)), period=period, interval="1d", auto_adjust=adjust,
                     progress=False, group_by="column", threads=True)
    c = px["Close"]
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c


def load_returns(period="5y"):
    """(fund daily returns, factor ETF daily returns) on shared 4 PM closes."""
    px = _download(FUND_POOL + [e for _, e in FACTORS], period)
    px = px[px["SPY"].notna()]
    rets = px.pct_change()
    return rets[[f for f in FUND_POOL if f in rets]], rets[[e for _, e in FACTORS]]


def composite(fund_rets, min_funds=6):
    """Equal-weight average of the funds that printed a return that day."""
    r = fund_rets.mean(axis=1, skipna=True)
    return r[fund_rets.notna().sum(axis=1) >= min_funds]


def ridge_fit(y, X, lam=RIDGE):
    """Exposure per factor (fraction of fund) and r2. Factors are put on equal vol first so the
    penalty does not favor the jumpy ones."""
    import numpy as np
    sd = X.std(axis=0)
    Xs = X / sd
    A = Xs.T @ Xs + lam * len(y) * np.eye(X.shape[1])
    b = np.linalg.solve(A, Xs.T @ y) / sd
    resid = y - X @ b
    return b, float(1 - resid.var() / y.var()) if y.var() > 0 else 0.0


def fit_latest(y, factor_rets, window=WINDOW, lag=0):
    """Exposures from the `window` days ending `lag` days before the last shared bar."""
    df = factor_rets.join(y.rename("y"), how="inner").dropna()
    if lag:
        df = df.iloc[:-lag]
    df = df.iloc[-window:]
    if len(df) < window:
        return None
    b, r2 = ridge_fit(df["y"].values, df.drop(columns="y").values)
    return {"bar_date": df.index[-1].date(), "r2": r2,
            "beta": {k: 100.0 * float(b[i]) for i, (k, _) in enumerate(FACTORS)}}


def daily_fit():
    """{'now': fit, 'prev': fit 5 bars earlier, 'dbmf': fit on DBMF alone, 'n_funds': n}."""
    funds, factors = load_returns("1y")
    y = composite(funds)
    out = {"now": fit_latest(y, factors), "prev": fit_latest(y, factors, lag=5),
           "n_funds": int(funds.iloc[-1].notna().sum())}
    if "DBMF" in funds:
        out["dbmf"] = fit_latest(funds["DBMF"].dropna(), factors)
    return out


def save_fit(conn, fit):
    if not fit or not fit.get("now"):
        return
    cur = conn.cursor()
    f = fit["now"]
    for k, v in f["beta"].items():
        cur.execute("""INSERT INTO sys_flow_fit (bar_date, market, beta_pct, r2, window_days, n_funds)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (bar_date, market) DO UPDATE SET beta_pct=EXCLUDED.beta_pct, r2=EXCLUDED.r2,
                         window_days=EXCLUDED.window_days, n_funds=EXCLUDED.n_funds, computed_at=now()""",
                    (f["bar_date"], k, round(v, 2), round(f["r2"], 3), WINDOW, fit.get("n_funds")))
    conn.commit()


# ── lookback weights ─────────────────────────────────────────────────────────

def lookback_weights():
    import numpy as np
    from scipy.optimize import nnls
    funds, factors = load_returns("5y")
    y = composite(funds)
    px = (1 + factors.fillna(0)).cumprod()
    vol = factors.rolling(60).std().shift(1)
    strat = {}
    for n in STUDY_LOOKBACKS:
        sig = np.sign(px.shift(1) / px.shift(1 + n) - 1)          # known at yesterday's close
        strat[n] = (sig * factors / vol).mean(axis=1) * 0.01       # each market sized to 1% daily vol
    import pandas as pd
    df = pd.DataFrame(strat).join(y.rename("y"), how="inner").dropna()
    X = df[list(STUDY_LOOKBACKS)].values
    w, _ = nnls(X, df["y"].values)
    fitted = X @ w
    r2 = float(1 - ((df["y"].values - fitted) ** 2).sum() / ((df["y"].values - df["y"].mean()) ** 2).sum())
    share = w / w.sum() if w.sum() > 0 else np.full(len(w), 1 / len(w))
    out = {"weights": {str(n): round(float(s), 3) for n, s in zip(STUDY_LOOKBACKS, share)},
           "r2": round(r2, 3), "corr": round(float(np.corrcoef(fitted, df["y"].values)[0, 1]), 3),
           "days": len(df), "from": str(df.index[0].date()), "to": str(df.index[-1].date())}
    os.makedirs(os.path.dirname(WEIGHTS_PATH), exist_ok=True)
    with open(WEIGHTS_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def load_weights():
    try:
        with open(WEIGHTS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── follow-through study ─────────────────────────────────────────────────────

def follow_through():
    import numpy as np
    syms = {m["yahoo"]: m for m in MARKETS if m.get("yahoo")}
    px = _download(list(syms), "max", adjust=False)
    cells = {}                                   # (class, lookback) -> [(fwd5, fwd10)]
    for sym, m in syms.items():
        s = px[sym].dropna()
        if len(s) < 1500:
            continue
        r = s.pct_change()
        vol = r.rolling(60).std()
        for n in STUDY_LOOKBACKS:
            above = (s > s.shift(n)).astype(int)
            flip = above.diff()                  # +1 = turned long, -1 = turned short
            for h in (5, 10):
                fwd = (s.shift(-h) / s - 1) / (vol * np.sqrt(h))
                ev = (flip * fwd)[flip.abs() == 1].dropna()
                cells.setdefault((m["cls"], n), {}).setdefault(h, []).extend(ev.values.tolist())
    out = {"rows": [], "unit": "next-N-bar move in the flip's direction, in units of that market's N-bar vol"}
    for cls in CLASSES + ["all"]:
        for n in STUDY_LOOKBACKS:
            row = {"cls": cls, "lookback": n}
            for h in (5, 10):
                if cls == "all":
                    v = [x for (c, k), d in cells.items() if k == n for x in d.get(h, [])]
                else:
                    v = cells.get((cls, n), {}).get(h, [])
                if len(v) < 30:
                    continue
                a = np.array(v)
                row[f"n{h}"] = len(a)
                row[f"avg{h}"] = round(float(a.mean()), 3)
                row[f"hit{h}"] = round(float((a > 0).mean() * 100), 1)
                row[f"t{h}"] = round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), 2)
            if "n5" in row:
                out["rows"].append(row)
    out["from"] = str(px.index[0].date())
    out["to"] = str(px.index[-1].date())
    os.makedirs(os.path.dirname(STUDY_PATH), exist_ok=True)
    with open(STUDY_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    return out


def load_study():
    try:
        with open(STUDY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def study_lines(st):
    if not st:
        return ["  not run yet: py -m tools.sys_flows_fit --study"]
    L = [f"  {st['from']} to {st['to']}. Score = move in the flip's direction over the next 5 / 10 bars, in vol units.",
         "  Above 0 with t over 2 = price really did follow through. hit = % of flips that went the right way.",
         f"  {'class':<10}{'lookback':>9}{'flips':>7}{'5-bar':>8}{'hit':>6}{'t':>6}{'10-bar':>8}{'hit':>6}{'t':>6}"]
    for r in st["rows"]:
        L.append(f"  {r['cls']:<10}{r['lookback']:>9}{r['n5']:>7}{r['avg5']:>+8.3f}{r['hit5']:>5.0f}%{r['t5']:>+6.1f}"
                 + (f"{r['avg10']:>+8.3f}{r['hit10']:>5.0f}%{r['t10']:>+6.1f}" if "n10" in r else ""))
    return L


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", action="store_true")
    ap.add_argument("--study", action="store_true")
    a = ap.parse_args(argv)
    if a.weights:
        print("lookback weights:", json.dumps(lookback_weights()))
    if a.study:
        print("FOLLOW-THROUGH AFTER A FLIP")
        print("\n".join(study_lines(follow_through())))
    if not (a.weights or a.study):
        f = daily_fit()
        for k in ("now", "prev", "dbmf"):
            if f.get(k):
                print(k, f[k]["bar_date"], f"r2 {f[k]['r2']:.2f}",
                      " ".join(f"{m}:{v:+.0f}" for m, v in f[k]["beta"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
