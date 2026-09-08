"""ml/t10_floor_replay.py — T10: replay Kris's 2026 book with exactly two
changes (the "April fix"):

  RULE 1  EXPOSURE FLOOR — whenever SPY closed above its TREND level on
          the prior bar (own corpus: ml_features.above_trend), a SPY
          sleeve tops total net-long exposure up to FLOOR (55%) of
          account equity. TREND off -> sleeve to zero.
  RULE 2  NO HEDGE BOOK — every short episode and every long position in
          an inverse/levered-inverse ETF is removed entirely.

Everything else is EXACTLY as traded: all clean long episodes keep
Kris's actual buys AND actual sells. Equity = Fidelity's own monthly
beginning balances (T9b source), stepped daily. The removed hedge
book's actual P&L is reported so the swap is transparent.

Output: total + monthly P&L (April called out), curve PNG (ACTUAL
whole-book vs T10 vs SPY-same-capital), reports/kris_t10_<date>.md.

    py ml/t10_floor_replay.py
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()
from ml.t7_kris_book import (load_trades, build_episodes, episode_stats,  # noqa: E402
                             load_px, MTM_DATE)

FLOOR = 0.55
INVERSE = {"SCO", "TBT", "TZA", "SQQQ", "SOXS", "EUO", "MSTZ", "SBIT",
           "SETH", "SJB", "SEF", "TYO", "DWSH", "BTAL", "SIJ", "MSFD",
           "BMNZ", "UDN", "DRIP", "SH", "PSQ", "RWM", "TBF", "UVXY",
           "VIXY", "SDS", "QID", "SPXU", "SPXS", "TWM", "EPV", "YXI"}
OUT = REPO / "reports"
LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


def fidelity_equity(cal) -> pd.Series:
    path = Path(r"C:\Users\bogac\Downloads\Investment_income_balance_detail.csv")
    months = {m: i + 1 for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
    beg = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.reader(fh):
            if r and re.match(r"^[A-Z][a-z]{2} 20\d\d", r[0] or ""):
                m = re.match(r"^([A-Z][a-z]{2}) (20\d\d)", r[0])
                beg[pd.Timestamp(int(m.group(2)), months[m.group(1)], 1)] = \
                    float(r[1].replace("$", "").replace(",", ""))
    s = pd.Series(beg).sort_index()
    return s.reindex(cal, method="ffill").bfill()


def book_curve(eps, px, cal):
    """Cumulative P&L and gross exposure for a set of episodes, using the
    ACTUAL legs (buys and sells)."""
    pnl = pd.Series(0.0, index=cal)
    expo = pd.Series(0.0, index=cal)
    for e in eps:
        c = px.get(e["symbol"])
        if c is None:
            continue
        cc = c.reindex(cal).ffill()
        qty = cash = 0.0
        legs = iter(sorted(e["legs"]))
        nxt = next(legs, None)
        for d0 in cal[cal >= e["open"]]:
            while nxt is not None and nxt[0] <= d0:
                _d, q, p, a = nxt
                qty += q
                cash += a
                nxt = next(legs, None)
            price = cc.loc[d0]
            if price != price:
                continue
            pnl[d0] += cash + qty * price
            # floor counts NET LONG dollars only: oversold episodes
            # (negative residual qty from partial fill data) must not
            # register as exposure
            expo[d0] += max(qty, 0.0) * price
    return pnl, expo


def main() -> int:
    trades = load_trades()
    eps = build_episodes(trades)
    syms = sorted({e["symbol"] for e in eps})
    px = load_px(syms + ["SPY"])
    spy = px["SPY"].dropna()
    led = pd.DataFrame([episode_stats(e, px, spy) for e in eps])
    ok = (~led["pre_window"] & ~led["cusip"] & ~led["mismatch"]
          & ~led["no_px"])
    cal = spy[(spy.index >= pd.Timestamp("2026-01-02"))
              & (spy.index <= MTM_DATE)].index

    is_hedge = (led["short"] | led["symbol"].isin(INVERSE))
    keep_eps = [e for e, k, h in zip(eps, ok, is_hedge) if k and not h]
    hedge_eps = [e for e, k, h in zip(eps, ok, is_hedge) if k and h]

    say(f"# T10 — exposure floor + no hedge book ({date.today()})")
    say(f"\nKept: {len(keep_eps)} long episodes exactly as traded (buys "
        f"AND sells). Removed: {len(hedge_eps)} hedge episodes (shorts + "
        f"inverse ETFs), plus the quarantined reverse-split bucket "
        f"(cash-flow {led[led['mismatch']]['pnl'].sum():+,.0f} USD, "
        f"largely inverse names) which was already outside the clean "
        f"ledger. FLOOR = {FLOOR:.0%} of Fidelity equity while SPY "
        f"closed above TREND on the prior bar; SPY sleeve daily-"
        f"rebalanced to the top-up target, no costs.")

    with db_pg.get_conn() as conn:
        at = pd.read_sql(
            "SELECT bar_date, above_trend FROM ml_features "
            "WHERE ticker='SPY' AND bar_date >= '2025-12-15'", conn)
    at["bar_date"] = pd.to_datetime(at["bar_date"])
    trend_on = (at.set_index("bar_date")["above_trend"].astype(float)
                .reindex(cal).ffill().shift(1).fillna(0) == 1)

    equity = fidelity_equity(cal)
    long_pnl, long_expo = book_curve(keep_eps, px, cal)
    hedge_pnl, _ = book_curve(hedge_eps, px, cal)

    spy_ret = spy.reindex(cal).pct_change().fillna(0.0)
    target = pd.Series(0.0, index=cal)
    mask = trend_on
    target[mask] = np.maximum(
        0.0, FLOOR * equity[mask] - long_expo[mask])
    sleeve = (target.shift(1).fillna(0.0) * spy_ret).cumsum()

    # ---- variant B: BETA-weighted floor ----
    # dollar exposure hides that much of the long book is cash-substitutes
    # (BUXX/CLOX) and anti-beta names (TAIL, BTAL): weight each name's
    # floor contribution by its full-period beta to SPY (disclosed
    # simplification), so the sleeve tops up SPY-EQUIVALENT exposure
    betas = {}
    for s in {e["symbol"] for e in keep_eps}:
        c = px.get(s)
        if c is None:
            continue
        r = (c.reindex(cal).ffill().pct_change()
             .replace([np.inf, -np.inf], np.nan))
        j = pd.concat([r, spy_ret], axis=1).dropna()
        if len(j) > 60 and j.iloc[:, 1].var() > 0:
            betas[s] = float(np.clip(
                j.cov().iloc[0, 1] / j.iloc[:, 1].var(), -1.0, 3.0))
    beta_expo = pd.Series(0.0, index=cal)
    for e in keep_eps:
        c = px.get(e["symbol"])
        b = betas.get(e["symbol"])
        if c is None or b is None:
            continue
        cc = c.reindex(cal).ffill()
        qty = 0.0
        legs = iter(sorted(e["legs"]))
        nxt = next(legs, None)
        for d0 in cal[cal >= e["open"]]:
            while nxt is not None and nxt[0] <= d0:
                qty += nxt[1]
                nxt = next(legs, None)
            price = cc.loc[d0]
            if price == price:
                beta_expo[d0] += max(qty, 0.0) * price * b
    target_b = pd.Series(0.0, index=cal)
    target_b[mask] = np.maximum(
        0.0, FLOOR * equity[mask] - beta_expo[mask])
    sleeve_b = (target_b.shift(1).fillna(0.0) * spy_ret).cumsum()

    t10 = long_pnl + sleeve
    t10b = long_pnl + sleeve_b
    actual = long_pnl + hedge_pnl

    say(f"\n## Results (cumulative P&L, USD)")
    say(f"{'':<24}{'ACTUAL':>10}{'T10':>10}{'of which sleeve':>17}")
    m = pd.DataFrame({"actual": actual, "t10": t10, "t10b": t10b,
                      "sleeve": sleeve, "sleeve_b": sleeve_b})
    m["month"] = m.index.to_period("M")
    prev = dict.fromkeys(("actual", "t10", "t10b", "sleeve_b"), 0.0)
    for mo, g in m.groupby("month"):
        d = {k: g[k].iloc[-1] - prev[k] for k in prev}
        star = "  <-- April" if str(mo) == "2026-04" else ""
        say(f"{str(mo):<24}{d['actual']:>+10,.0f}{d['t10']:>+10,.0f}"
            f"{g['t10b'].iloc[-1] - prev['t10b']:>+10,.0f}"
            f"{d['sleeve_b']:>+17,.0f}{star}")
        prev = {k: g[k].iloc[-1] for k in prev}
    say(f"\n{'TOTAL':<24}{actual.iloc[-1]:>+10,.0f}{t10.iloc[-1]:>+10,.0f}"
        f"{t10b.iloc[-1]:>+10,.0f}{sleeve_b.iloc[-1]:>+17,.0f}")
    say(f"(columns: ACTUAL | T10 dollar-floor | T10b BETA-floor | "
        f"of which beta-sleeve)")
    say(f"Beta-floor sleeve avg when TREND on: "
        f"{target_b[mask].mean():,.0f} USD "
        f"({target_b[mask].mean() / equity.mean() * 100:.0f}% of equity); "
        f"long book's own avg beta-exposure "
        f"{beta_expo.mean() / equity.mean() * 100:.0f}% of equity vs "
        f"{long_expo.mean() / equity.mean() * 100:.0f}% in dollars.")
    say(f"Hedge book removed (its actual P&L): {hedge_pnl.iloc[-1]:+,.0f} "
        f"USD. Long book kept as-is: {long_pnl.iloc[-1]:+,.0f} USD.")
    say(f"TREND was ON {trend_on.mean()*100:.0f}% of 2026 days; average "
        f"sleeve size when on: "
        f"{target[mask].mean():,.0f} USD "
        f"({target[mask].mean() / equity.mean() * 100:.0f}% of equity).")
    dd_a = (actual - actual.cummax()).min()
    dd_t = (t10 - t10.cummax()).min()
    say(f"Max P&L drawdown: ACTUAL {dd_a:+,.0f} USD vs T10 {dd_t:+,.0f} "
        f"USD (vs ~{equity.mean():,.0f} equity).")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(cal, actual.values, label="ACTUAL (longs + hedge book)", lw=1.7)
    ax.plot(cal, t10.values, label="T10 ($-floor + no hedges)", lw=1.5)
    ax.plot(cal, t10b.values, label="T10b (BETA-floor + no hedges)", lw=1.9)
    ax.plot(cal, long_pnl.values, label="long book alone", lw=1.2,
            alpha=0.7)
    ax.axhline(0, color="gray", lw=0.6)
    ax.set_title("T10 — the April fix: exposure floor + no hedge book")
    ax.legend(); ax.grid(alpha=0.3)
    p1 = OUT / f"t10_floor_{date.today().isoformat()}.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight"); plt.close(fig)
    say(f"\nPNG: {p1.name}")
    say("\nCaveats: sleeve is daily-rebalanced with no costs (a weekly "
        "version would differ slightly); equity = Fidelity monthly "
        "beginning balances stepped daily; the quarantined reverse-split "
        "bucket is outside both ACTUAL and T10; dividends excluded on "
        "positions, included in nothing; TREND = ml_features SPY "
        "above_trend, prior bar, no lookahead.")

    p = OUT / f"kris_t10_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
