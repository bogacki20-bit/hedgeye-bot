"""ml/t6_rerank_pa.py — T6: Keith's PA from the Daily ETF Re-Rank.

Coverage, then daily portfolios (positions set at each issue's close,
daily rebalance to target between issues):
  INTEGRATED-PA  delta-integration of commentary moves (bought/sold X
                 bps; sold_all -> 0; add_min -> +25bps ASSUMPTION).
                 Stated position LEVELS are image-only, so this is the
                 closest text-derivable "exact" book; starts empty at
                 the first issue (burn-in shows as time-in-cash).
  TOP-10/TOP-20  equal weight of the top-N ranked (FDRXX -> BIL proxy),
                 rebalanced every issue.
  EW-UNIVERSE    equal weight of the full ranked list each issue.
  Benchmarks: SPY B&H, 60/40 SPY/TLT (monthly rebalance).
Sizing-band-enforced variant (c) is NOT buildable: the asset-class
bands ship only in the table image (0% of issues carry them in text).
All prices yfinance ADJUSTED closes (total-return-ish), cached.
Rank-riser test: top-5 rank risers over 5 issues -> fwd_ret_20 vs the
issue's full ranked list, bootstrap 90% CI.

    py ml/t6_rerank_pa.py
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

NBOOT = 10_000
ADD_MIN_BPS = 25
OUT = REPO / "reports"
CACHE = REPO / "data" / "reference" / "t6_px_adj.csv"
LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


def load_rerank():
    with db_pg.get_conn() as conn:
        rr = pd.read_sql(
            "SELECT note_date, ticker, rank, move_bps, action "
            "FROM hedgeye_rerank ORDER BY note_date, rank", conn)
    rr["note_date"] = pd.to_datetime(rr["note_date"])
    return rr


def load_px(tickers):
    import yfinance as yf
    if CACHE.exists():
        px = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    else:
        px = pd.DataFrame()
    need = [t for t in tickers if t not in px.columns]
    if need:
        for i in range(0, len(need), 50):
            batch = need[i:i + 50]
            df = yf.download(batch, period="4y", interval="1d",
                             auto_adjust=True, progress=False, threads=True)
            close = df["Close"] if "Close" in df.columns.get_level_values(0) \
                else df
            if isinstance(close, pd.Series):
                close = close.to_frame(batch[0])
            for t in batch:
                if t in close.columns:
                    s = close[t].dropna()
                    if len(s) > 100:
                        px[t] = s
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        px.to_csv(CACHE)
    return px.sort_index()


def metrics(r):
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    return cagr, dd, sh, eq


def run_portfolio(wtargets, ret, name):
    """wtargets: DataFrame indexed by issue date, columns tickers, target
    weights. Applied from the NEXT day, daily-rebalanced to target."""
    w = wtargets.reindex(ret.index).ffill().fillna(0.0)
    port = (w.shift(1) * ret).sum(axis=1)
    port = port[w.shift(1).abs().sum(axis=1) > 0]
    to = (w.diff().abs().sum(axis=1) / 2)
    ann_to = to.sum() / (len(port) / 252) if len(port) else np.nan
    tic = 1 - w.sum(axis=1).reindex(port.index).mean()
    return port, ann_to, tic


def boot_ci(a, b, rng):
    if len(a) < 20 or len(b) < 20:
        return None
    d = (rng.choice(a, (NBOOT, len(a))).mean(axis=1)
         - rng.choice(b, (NBOOT, len(b))).mean(axis=1))
    return float(np.percentile(d, 5)), float(np.percentile(d, 95))


def main() -> int:
    rng = np.random.default_rng(11)
    rr = load_rerank()
    say(f"# T6 — Re-Rank PA study ({date.today()})")

    say("\n## Coverage")
    issues = rr[rr["rank"].notna()].groupby("note_date")["ticker"].count()
    say(f"Issues: {len(issues)}, {issues.index.min().date()} .. "
        f"{issues.index.max().date()}; tickers/issue mean "
        f"{issues.mean():.1f} (min {issues.min()}, max {issues.max()}); "
        f"distinct tickers {rr['ticker'].nunique()}.")
    per_m = issues.resample("ME").size()
    say("Issues per month: " + ", ".join(
        f"{i.strftime('%Y-%m')}:{v}" for i, v in per_m.items() if v))
    n_moves = rr["move_bps"].notna().sum()
    n_act = rr["action"].notna().sum()
    say(f"\nExplicit sizes: {n_moves} bps-moves + {n_act} "
        f"sold_all/add_min actions across {len(issues)} issues "
        f"({(n_moves + n_act) / len(rr) * 100:.1f}% of rows). Position "
        f"LEVELS and asset-class sizing bands are image-only (0% in "
        f"text) — variant (c) not buildable; move deltas are the only "
        f"stated sizes.")

    tickers = sorted(rr["ticker"].unique())
    say(f"\nFetching adjusted closes for {len(tickers)} tickers "
        f"(FDRXX -> BIL cash proxy)...")
    px = load_px([t for t in tickers if t != "FDRXX"] + ["BIL", "SPY", "TLT"])
    have = set(px.columns)
    missing = [t for t in tickers if t != "FDRXX" and t not in have]
    say(f"Price data: {len(have)} tickers; MISSING {len(missing)}: "
        f"{', '.join(missing) or 'none'} (weight goes to cash).")
    ret = px.pct_change(fill_method=None)
    ret = ret[ret.index >= issues.index.min()]

    def col(t):
        return "BIL" if t == "FDRXX" else t

    # --- weight targets per issue ---
    ranked = rr[rr["rank"].notna()]
    top10_w, top20_w, ew_w, pa_w = {}, {}, {}, {}
    book: dict[str, float] = {}
    for nd, grp in rr.groupby("note_date"):
        g = grp[grp["rank"].notna()].sort_values("rank")
        names = [col(t) for t in g["ticker"] if col(t) in have]
        for N, store in ((10, top10_w), (20, top20_w)):
            sel = names[:N]
            store[nd] = {t: 1 / N for t in sel}
        ew_w[nd] = {t: 1 / len(names) for t in names} if names else {}
        # integrated PA
        for _, row in grp.iterrows():
            t = col(row["ticker"])
            if row["action"] == "sold_all":
                book[t] = 0.0
            elif row["action"] == "add_min":
                book[t] = book.get(t, 0.0) + ADD_MIN_BPS
            elif pd.notna(row["move_bps"]):
                book[t] = max(0.0, book.get(t, 0.0) + float(row["move_bps"]))
        pa_w[nd] = {t: b / 10000 for t, b in book.items()
                    if b > 0 and t in have}

    say("\n## Portfolios (positions from each issue's close, daily "
        "rebalance to target)")
    say(f"{'portfolio':<15}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}"
        f"{'turnover/yr':>12}{'time-in-cash':>14}")
    curves = {}
    for name, wt in (("INTEGRATED-PA", pa_w), ("TOP-10", top10_w),
                     ("TOP-20", top20_w), ("EW-UNIVERSE", ew_w)):
        W = pd.DataFrame(wt).T.fillna(0.0)
        W.index = pd.to_datetime(W.index)
        port, ann_to, tic = run_portfolio(W, ret, name)
        cagr, dd, sh, eq = metrics(port)
        curves[name] = eq
        say(f"{name:<15}{cagr*100:+7.1f}%{dd*100:+7.1f}%{sh:8.2f}"
            f"{ann_to:11.1f}x{tic*100:13.1f}%")
    # benchmarks over the same window
    win = curves["TOP-10"].index
    spy = ret["SPY"].reindex(win).dropna()
    cagr, dd, sh, eq = metrics(spy)
    curves["SPY B&H"] = eq
    say(f"{'SPY B&H':<15}{cagr*100:+7.1f}%{dd*100:+7.1f}%{sh:8.2f}"
        f"{'—':>12}{'0.0%':>14}")
    mrb = pd.Series(index=win, dtype=float)
    w6040 = pd.DataFrame(0.0, index=win, columns=["SPY", "TLT"])
    month = None
    wspy, wtlt = 0.6, 0.4
    for d in win:
        if month != (d.year, d.month):
            wspy, wtlt, month = 0.6, 0.4, (d.year, d.month)
        w6040.loc[d] = [wspy, wtlt]
    port6040 = (w6040.shift(1) * ret[["SPY", "TLT"]].reindex(win)).sum(axis=1).dropna()
    cagr, dd, sh, eq = metrics(port6040)
    curves["60/40"] = eq
    say(f"{'60/40':<15}{cagr*100:+7.1f}%{dd*100:+7.1f}%{sh:8.2f}"
        f"{'—':>12}{'0.0%':>14}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for name, eq in curves.items():
        ax.plot(eq.index, eq.values, lw=1.6, label=name)
    ax.set_title("T6 — Re-Rank portfolios vs benchmarks (adjusted closes)")
    ax.legend(); ax.grid(alpha=0.3)
    p1 = OUT / f"t6_curves_{date.today().isoformat()}.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight"); plt.close(fig)
    say(f"\nequity-curve PNG: {p1.name}")

    say("\n## Rank-riser test — top-5 risers over 5 issues, fwd_ret_20")
    piv = ranked.pivot_table(index="note_date", columns="ticker",
                             values="rank")
    idates = list(piv.index)
    riser_rets, base_rets = [], []
    for i in range(5, len(idates)):
        cur, prev = piv.loc[idates[i]], piv.loc[idates[i - 5]]
        both = cur.dropna().index.intersection(prev.dropna().index)
        if len(both) < 8:
            continue
        delta = (prev[both] - cur[both]).sort_values(ascending=False)
        risers = [t for t in delta.index[:5] if delta[t] > 0]
        d0 = idates[i]
        for t in cur.dropna().index:
            c = px.get(col(t))
            if c is None:
                continue
            ix = c.index.searchsorted(d0)
            if ix >= len(c) or ix + 20 >= len(c):
                continue
            f = c.iloc[ix + 20] / c.iloc[ix] - 1
            if f == f:
                (riser_rets if t in risers else base_rets).append(f)
    r, b = np.array(riser_rets), np.array(base_rets)
    rh, bh = (r > 0).astype(float), (b > 0).astype(float)
    ci = boot_ci(rh, bh, rng)
    ci_s = f"[{ci[0]*100:+.1f},{ci[1]*100:+.1f}]" if ci else "thin"
    star = " **" if ci and (ci[0] > 0 or ci[1] < 0) else ""
    say(f"risers n={len(r)} hit={rh.mean()*100:.1f}% "
        f"ret={r.mean()*100:+.2f}%  vs rest n={len(b)} "
        f"hit={bh.mean()*100:.1f}% ret={b.mean()*100:+.2f}%  "
        f"hit-diff CI {ci_s}{star}")
    say("\nGuards: single pooled CI for the riser test (no cell mining); "
        "riser/rest bars share issue days — anti-conservative. "
        "INTEGRATED-PA carries the add_min=+25bps assumption and an "
        "empty-book start (levels are image-only); portfolios ignore "
        "intra-issue drift (daily rebalance to target) and costs.")

    p = OUT / f"ml_round2_T6_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
