"""ml/t345_tag_study.py — T3/T4/T5: follow-the-tag equity curves,
tag-transition events, and the 60-bar horizon check.

T3  Per daily-core instrument, three daily strategies over the tag
    archive (2023-04 -> 2026-09): (a) buy-and-hold, (b) FOLLOW = long
    when tag bullish else flat, (c) FOLLOW-SHORT = long/short/flat.
    Positions change at the close of the tag day (mail lands pre-open;
    the day's tag is known by that close), so pos[t] earns ret[t+1].
    Gross and net of 5bps per side (charged on |dpos|). CAGR, maxDD,
    Sharpe, time-in-market; pooled equal-weight; equity-curve PNGs.
T4  Events = tag flips per instrument from the RAW signal sequence
    (consecutive signals <=7 calendar days apart): bearish->bullish,
    bullish->bearish, neutral->bullish. fwd_ret_5/20/60 + hit rate
    vs the instrument's all-signal-days baseline, bootstrap 90% CIs;
    plus flips-to-bullish with the UNDERLYING in the bottom third of
    that day's range.
T5  T2 tests (ii) and (iv) repeated at fwd_ret_60 (TREND is a >=3-month
    call; 20 bars may be unfair to the tag).

TLT is bridged INVERTED via UST30Y (the yield): TLT-bullish = yield-tag
BEARISH, cheap = yield in the TOP third. Same multiple-testing guards.

    py ml/t345_tag_study.py
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
COST = 0.0005          # 5 bps per side
OUT = REPO / "reports"
LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


# name -> (rr_instrument, trade_px_ticker, range_underlying, invert, flag)
UNIVERSE = {
    "SPY":    ("SPX",    "SPY",      "^GSPC",    False, ""),
    "QQQ":    ("COMPQ",  "QQQ",      "^IXIC",    False, ""),
    "IWM":    ("RUT",    "IWM",      "^RUT",     False, ""),
    "UUP":    ("USD",    "UUP",      "DX-Y.NYB", False, ""),
    "USO":    ("WTIC",   "USO",      "CL=F",     False, ""),
    "GLD":    ("GOLD",   "GLD",      "GC=F",     False, ""),
    "AAAU":   ("GOLD",   "AAAU",     "GC=F",     False, ""),
    "HYG":    ("HYG",    "HYG",      "HYG",      False, ""),
    "XLK":    ("XLK",    "XLK",      "XLK",      False, "tag ends 08/11/26"),
    "TLT":    ("UST30Y", "TLT",      "^TYX",     True,  "THIN 36%, inverted"),
    "XLU":    ("XLU",    "XLU",      "XLU",      False, "partial 47%"),
    "XLE":    ("XLE",    "XLE",      "XLE",      False, "partial 36%"),
    "AAPL":   ("AAPL",   "AAPL",     "AAPL",     False, ""),
    "AMZN":   ("AMZN",   "AMZN",     "AMZN",     False, ""),
    "GOOGL":  ("GOOGL",  "GOOGL",    "GOOGL",    False, ""),
    "META":   ("META",   "META",     "META",     False, ""),
    "MSFT":   ("MSFT",   "MSFT",     "MSFT",     False, ""),
    "NFLX":   ("NFLX",   "NFLX",     "NFLX",     False, ""),
    "NVDA":   ("NVDA",   "NVDA",     "NVDA",     False, ""),
    "TSLA":   ("TSLA",   "TSLA",     "TSLA",     False, ""),
    "DAX":    ("DAX",    "^GDAXI",   "^GDAXI",   False, ""),
    "NIKK":   ("NIKK",   "^N225",    "^N225",    False, ""),
    "COPPER": ("COPPER", "HG=F",     "HG=F",     False, ""),
    "SILVER": ("SILVER", "SI=F",     "SI=F",     False, ""),
    "NATGAS": ("NATGAS", "NG=F",     "NG=F",     False, ""),
}


def load_all():
    with db_pg.get_conn() as conn:
        trend = pd.read_sql(
            "SELECT date, ticker, tag FROM hedgeye_trend_daily", conn)
        rr = pd.read_sql(
            "SELECT ticker, signal_date, trend, buy_trade, sell_trade "
            "FROM hedgeye_risk_ranges WHERE trend IS NOT NULL", conn)
        px = pd.read_sql("SELECT ticker, bar_date, c FROM px_daily", conn)
    trend["date"] = pd.to_datetime(trend["date"])
    rr["signal_date"] = pd.to_datetime(rr["signal_date"])
    px["bar_date"] = pd.to_datetime(px["bar_date"])
    px["c"] = px["c"].astype(float)
    return trend, rr, px


def closes(px, t):
    return (px[px.ticker == t].set_index("bar_date")["c"]
            .sort_index())


def tag_map(tag, invert, short):
    """tag string -> position."""
    if invert:
        long_tag, short_tag = "BEARISH", "BULLISH"
    else:
        long_tag, short_tag = "BULLISH", "BEARISH"
    if tag == long_tag:
        return 1.0
    if tag == short_tag:
        return -1.0 if short else 0.0
    return 0.0


def metrics(r, pos=None):
    r = r.dropna()
    if len(r) < 60:
        return None
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else np.nan
    tim = float((pos != 0).mean()) if pos is not None else 1.0
    return cagr, dd, sh, tim, eq


def boot_ci(a, b, rng):
    if len(a) < 20 or len(b) < 20:
        return None
    d = (rng.choice(a, (NBOOT, len(a))).mean(axis=1)
         - rng.choice(b, (NBOOT, len(b))).mean(axis=1))
    return float(np.percentile(d, 5)), float(np.percentile(d, 95))


def t3(trend, px, rng):
    say("\n## T3 — follow-the-tag equity curves (2023-04 -> 2026-09)")
    say("Positions change at the tag day's close; gross, then net of "
        "5bps/side on turnover. B&H measured over the same window.")
    say(f"\n{'ticker':<8}{'strategy':<14}{'CAGR':>8}{'maxDD':>8}"
        f"{'Sharpe':>8}{'TiM':>6}   net: {'CAGR':>7}{'Sharpe':>8}")
    curves, pooled = {}, {}
    for name, (instr, trad, _und, inv, flag) in UNIVERSE.items():
        tg = (trend[trend.ticker == instr].set_index("date")["tag"]
              .sort_index())
        if tg.empty:
            continue
        c = closes(px, trad)
        idx = c.index[(c.index >= tg.index.min())
                      & (c.index <= tg.index.max())]
        ret = c.pct_change().reindex(idx)
        tagd = tg.reindex(idx)
        rows = {}
        for label, short in (("FOLLOW", False), ("FOLLOW-SHORT", True)):
            pos = tagd.map(lambda t: tag_map(t, inv, short)
                           if isinstance(t, str) else 0.0).fillna(0.0)
            strat = (pos.shift(1) * ret).fillna(0.0)
            net = strat - pos.diff().abs().fillna(0.0) * COST
            rows[label] = (strat, net, pos)
        bh = ret.fillna(0.0)
        rows["B&H"] = (bh, bh, pd.Series(1.0, index=idx))
        curves[name] = {}
        for label in ("B&H", "FOLLOW", "FOLLOW-SHORT"):
            strat, net, pos = rows[label]
            m = metrics(strat, pos)
            mn = metrics(net, pos)
            if m is None:
                continue
            cagr, dd, sh, tim, eq = m
            ncagr, _nd, nsh, _t, _neq = mn
            curves[name][label] = eq
            pooled.setdefault(label, []).append(strat.rename(name))
            say(f"{name:<8}{label:<14}{cagr*100:+7.1f}%{dd*100:+7.1f}%"
                f"{sh:8.2f}{tim*100:5.0f}%        {ncagr*100:+6.1f}%"
                f"{nsh:8.2f}  {flag if label == 'B&H' else ''}")
        say("")

    say("### Pooled equal-weight (mean daily return of active instruments)")
    say(f"{'strategy':<14}{'CAGR':>8}{'maxDD':>8}{'Sharpe':>8}")
    pooled_eq = {}
    for label in ("B&H", "FOLLOW", "FOLLOW-SHORT"):
        df = pd.concat(pooled[label], axis=1)
        pr = df.mean(axis=1, skipna=True).dropna()
        cagr, dd, sh, _t, eq = metrics(pr)
        pooled_eq[label] = eq
        say(f"{label:<14}{cagr*100:+7.1f}%{dd*100:+7.1f}%{sh:8.2f}")

    # PNGs
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 6))
    for label, eq in pooled_eq.items():
        ax.plot(eq.index, eq.values, label=label, lw=1.6)
    ax.set_title("T3 pooled equal-weight equity (gross)")
    ax.legend(); ax.grid(alpha=0.3); ax.set_yscale("log")
    p1 = OUT / f"t3_pooled_{date.today().isoformat()}.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight"); plt.close(fig)

    names = list(curves)
    ncol = 5
    nrow = -(-len(names) // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(4 * ncol, 2.6 * nrow))
    for ax, name in zip(axes.flat, names):
        for label, eq in curves[name].items():
            ax.plot(eq.index, eq.values, lw=1.0,
                    label=label if name == names[0] else None)
        ax.set_title(name, fontsize=9); ax.grid(alpha=0.3)
        ax.tick_params(labelsize=6)
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    fig.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    p2 = OUT / f"t3_curves_{date.today().isoformat()}.png"
    fig.savefig(p2, dpi=110, bbox_inches="tight"); plt.close(fig)
    say(f"\nequity-curve PNGs: {p1.name}, {p2.name}")
    return p1, p2


def fwd_at(c, d, n):
    ix = c.index.searchsorted(d)
    if ix >= len(c) or c.index[ix] != d or ix + n >= len(c):
        return np.nan
    return c.iloc[ix + n] / c.iloc[ix] - 1


def t4(rr, px, rng, counters):
    say("\n## T4 — tag transitions (raw signal sequence, gap <=7cd)")
    say("Event = flip at the signal day's close; fwd from that close. "
        "Baseline = all signal days for the instrument.")
    flips = {"bear->bull": [], "bull->bear": [], "neut->bull": []}
    cond_low3 = []
    base = {5: [], 20: [], 60: []}
    for name, (instr, trad, und, inv, _f) in UNIVERSE.items():
        sig = (rr[rr.ticker == instr]
               .sort_values("signal_date")
               [["signal_date", "trend", "buy_trade", "sell_trade"]])
        if sig.empty:
            continue
        c = closes(px, trad)
        u = closes(px, und)
        sig = sig.reset_index(drop=True)
        for n in (5, 20, 60):
            base[n] += [f for f in
                        (fwd_at(c, d, n) for d in sig["signal_date"])
                        if f == f]
        prev_tag, prev_d = None, None
        for _, row in sig.iterrows():
            d, tag = row["signal_date"], row["trend"].upper()
            if inv:
                tag = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}.get(tag, tag)
            if prev_tag is not None and (d - prev_d).days <= 7 \
                    and tag != prev_tag:
                key = None
                if prev_tag == "BEARISH" and tag == "BULLISH":
                    key = "bear->bull"
                elif prev_tag == "BULLISH" and tag == "BEARISH":
                    key = "bull->bear"
                elif prev_tag == "NEUTRAL" and tag == "BULLISH":
                    key = "neut->bull"
                if key:
                    ev = {n: fwd_at(c, d, n) for n in (5, 20, 60)}
                    flips[key].append(ev)
                    if key in ("bear->bull", "neut->bull"):
                        lo, hi = float(row["buy_trade"] or np.nan), \
                                 float(row["sell_trade"] or np.nan)
                        ux = u[u.index == d]
                        if len(ux) and hi > lo:
                            pos = (ux.iloc[0] - lo) / (hi - lo)
                            pos = 1 - pos if inv else pos
                            if pos <= 1 / 3:
                                cond_low3.append(ev)
            prev_tag, prev_d = tag, d

    say(f"\n{'event':<22}{'hzn':>4}{'n':>6}{'hit':>7}{'ret':>8}"
        f"{'baseHit':>9}  hit-diff 90% CI")
    for key, evs in list(flips.items()) + [("to-bull & low3", cond_low3)]:
        for n in (5, 20, 60):
            f = np.array([e[n] for e in evs if e[n] == e[n]])
            b = np.array(base[n])
            if len(f) == 0:
                say(f"{key:<22}{n:>4}     0")
                continue
            fh, bh = (f > 0).astype(float), (b > 0).astype(float)
            ci = boot_ci(fh, bh, rng)
            if ci:
                counters["tested"] += 1
                counters["positive"] += ci[0] > 0
                counters["negative"] += ci[1] < 0
            ci_s = f"[{ci[0]*100:+5.1f},{ci[1]*100:+5.1f}]" if ci else "thin"
            star = " **" if ci and (ci[0] > 0 or ci[1] < 0) else ""
            thin = " (n<100)" if len(f) < 100 else ""
            say(f"{key:<22}{n:>4}{len(f):>6}{fh.mean()*100:6.1f}%"
                f"{f.mean()*100:+7.2f}%{bh.mean()*100:8.1f}%  "
                f"{ci_s}{star}{thin}")


def t5(trend, rr, px, rng, counters):
    say("\n## T5 — T2 (ii)/(iv) at fwd_ret_60 (fair-horizon check)")
    say(f"{'ticker':<8}{'test':<12}{'n':>6}{'hit':>7}{'ret60':>8}"
        f"{'base':>7}  hit-diff 90% CI")
    pooled = {"base": [], "ii": [], "iv": []}
    for name, (instr, trad, und, inv, _f) in UNIVERSE.items():
        tg = trend[trend.ticker == instr][["date", "tag"]]
        if tg.empty:
            continue
        c = closes(px, trad)
        f60 = (c.shift(-60) / c - 1).rename("fwd60").reset_index()
        d = f60.merge(tg.rename(columns={"date": "bar_date"}),
                      on="bar_date", how="inner").dropna(subset=["fwd60"])
        want = "BEARISH" if inv else "BULLISH"
        d["bull"] = d["tag"] == want
        u = closes(px, und).rename("u_close").reset_index()
        r = (rr[rr.ticker == instr]
             .sort_values("signal_date")
             [["signal_date", "buy_trade", "sell_trade"]].dropna())
        m = pd.merge_asof(u, r, left_on="bar_date", right_on="signal_date",
                          direction="backward",
                          tolerance=pd.Timedelta(days=7))
        lo, hi = m["buy_trade"].astype(float), m["sell_trade"].astype(float)
        pos = (m["u_close"] - lo) / (hi - lo)
        pos[hi <= lo] = np.nan
        d = d.merge(pd.DataFrame({"bar_date": m["bar_date"], "pos": pos}),
                    on="bar_date", how="left")
        d["cheap"] = ((1 - d["pos"]) if inv else d["pos"]) <= (1 / 3)
        base = d
        pooled["base"].append(base)
        for test, sel in (("(ii) bull", d["bull"]),
                          ("(iv) b+low3", d["bull"] & (d["cheap"] == True))):  # noqa: E712
            sub = d[sel]
            pooled["ii" if "(ii)" in test else "iv"].append(sub)
            if len(sub) == 0:
                say(f"{name:<8}{test:<12}     0")
                continue
            sh = (sub["fwd60"] > 0).astype(float).to_numpy()
            bh = (base["fwd60"] > 0).astype(float).to_numpy()
            ci = boot_ci(sh, bh, rng)
            if ci:
                counters["tested"] += 1
                counters["positive"] += ci[0] > 0
                counters["negative"] += ci[1] < 0
            ci_s = f"[{ci[0]*100:+5.1f},{ci[1]*100:+5.1f}]" if ci else " thin "
            star = " **" if ci and (ci[0] > 0 or ci[1] < 0) else ""
            thin = " (n<100)" if len(sub) < 100 else ""
            say(f"{name:<8}{test:<12}{len(sub):>6}{sh.mean()*100:6.1f}%"
                f"{sub['fwd60'].mean()*100:+7.2f}%"
                f"{bh.mean()*100:6.1f}%  {ci_s}{star}{thin}")
    say("\nPooled at 60 bars:")
    bass = pd.concat(pooled["base"])
    for key, label in (("ii", "(ii) tag bullish"),
                       ("iv", "(iv) bull+bottom-third")):
        sub = pd.concat([s for s in pooled[key] if len(s)])
        sh = (sub["fwd60"] > 0).astype(float).to_numpy()
        bh = (bass["fwd60"] > 0).astype(float).to_numpy()
        ci = boot_ci(sh, bh, rng)
        ci_s = f"[{ci[0]*100:+5.1f},{ci[1]*100:+5.1f}]" if ci else "thin"
        star = " **" if ci and (ci[0] > 0 or ci[1] < 0) else ""
        say(f"POOLED  {label:<24} n={len(sub):>6} hit={sh.mean()*100:5.1f}% "
            f"ret={sub['fwd60'].mean()*100:+6.2f}% vs {bh.mean()*100:5.1f}%"
            f"  CI {ci_s}{star}")


def main() -> int:
    rng = np.random.default_rng(11)
    trend, rr, px, = load_all()
    say(f"# T3/T4/T5 — follow-the-tag study ({date.today()})")
    say("\nUniverse: 12 bridged ETFs + 8 megacaps + DAX/NIKK/COPPER/"
        "SILVER/NATGAS. TLT inverted via UST30Y (thin). No costs unless "
        "stated; net pass = 5bps/side on turnover.")
    counters = {"tested": 0, "positive": 0, "negative": 0}
    t3(trend, px, rng)
    t4(rr, px, rng, counters)
    t5(trend, rr, px, rng, counters)
    say("\n## Multiple-testing guard (T4+T5 CIs)")
    say(f"CIs computed: {counters['tested']}; chance-expected clears at "
        f"90% ~{counters['tested']*0.05:.1f}. Observed "
        f"{counters['positive']} positive / {counters['negative']} "
        f"negative excluding zero. Pooled rows share macro days "
        f"(anti-conservative).")
    p = OUT / f"ml_round2_T3-5_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
