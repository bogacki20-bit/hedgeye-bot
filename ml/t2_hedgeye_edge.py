"""ml/t2_hedgeye_edge.py — T2: does Hedgeye's direction carry forward
edge, and does MFR/LRR timing add to it?

Part A (bridged, MFR available): per ETF mapped to a Risk Range
instrument, fwd_ret_20 / hit rate for
  (i)   all bars inside the instrument's tag-coverage window (baseline)
  (ii)  Hedgeye tag BULLISH (same-day tag; the mail lands pre-open,
        entry at the close)
  (iii) bullish AND setup_lrr (above_trend & rp < 0.45)
  (iv)  bullish AND close in the BOTTOM THIRD of Keith's own range,
        position computed on the UNDERLYING's price (GC=F, CL=F,
        DX-Y.NYB, ^GSPC, ^IXIC, ^RUT, ^TYX...), never ETF-vs-spot
  (v)   bullish AND commodity own-vol top band (USO: OVX>42,
        GLD/AAAU: GVZ>18) — commodities only
Part B (tag-only, no MFR): (i)/(ii)/(iv) on the 8 megacaps +
DAX/NIKK/COPPER/SILVER/NATGAS via free daily closes.

TLT bridge is INVERTED: Keith quotes UST30Y (the yield) — TLT-bullish =
yield-tag BEARISH, and "cheap" = yield in the TOP third of its range.
Bootstrap 90% CIs of the hit-rate difference vs (i), per ticker and
pooled, with the chance-expected count. fwd_ret_20 = c[t+20]/c[t]-1
(ml_targets for enrolled tickers, px_daily closes otherwise).

    py ml/t2_hedgeye_edge.py
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
OUT = REPO / "reports"
LINES: list[str] = []


def say(s=""):
    print(s)
    LINES.append(s)


# etf -> (rr_instrument, underlying_px_ticker, invert, has_mfr, flag)
BRIDGES = {
    "SPY":  ("SPX",    "^GSPC",    False, True,  ""),
    "QQQ":  ("COMPQ",  "^IXIC",    False, True,  ""),
    "IWM":  ("RUT",    "^RUT",     False, True,  ""),
    "UUP":  ("USD",    "DX-Y.NYB", False, True,  ""),
    "USO":  ("WTIC",   "CL=F",     False, True,  ""),
    "GLD":  ("GOLD",   "GC=F",     False, True,  ""),
    "AAAU": ("GOLD",   "GC=F",     False, True,  ""),
    "HYG":  ("HYG",    "HYG",      False, False, "no-MFR (held)"),
    "XLK":  ("XLK",    "XLK",      False, True,  "tag ends 2026-08-11"),
    "TLT":  ("UST30Y", "^TYX",     True,  True,  "THIN 36% + inverted"),
    "XLU":  ("XLU",    "XLU",      False, True,  "partial 47%"),
    "XLE":  ("XLE",    "XLE",      False, True,  "partial 36%"),
}
BRENT_CHECK = ("USO(BRENT)", "BRENT", "BZ=F")
OWN_VOL = {"USO": ("^OVX", 42.0), "GLD": ("^GVZ", 18.0),
           "AAAU": ("^GVZ", 18.0)}
TAG_ONLY = {
    **{t: (t, t) for t in ("AAPL", "AMZN", "GOOGL", "META", "MSFT",
                           "NFLX", "NVDA", "TSLA")},
    "DAX": ("DAX", "^GDAXI"), "NIKK": ("NIKK", "^N225"),
    "COPPER": ("COPPER", "HG=F"), "SILVER": ("SILVER", "SI=F"),
    "NATGAS": ("NATGAS", "NG=F"),
}


def load_all():
    with db_pg.get_conn() as conn:
        trend = pd.read_sql(
            "SELECT date, ticker, tag FROM hedgeye_trend_daily", conn)
        rr = pd.read_sql(
            "SELECT ticker, signal_date, buy_trade, sell_trade "
            "FROM hedgeye_risk_ranges "
            "WHERE buy_trade IS NOT NULL AND sell_trade IS NOT NULL", conn)
        px = pd.read_sql(
            "SELECT ticker, bar_date, c FROM px_daily", conn)
        feats = pd.read_sql(
            "SELECT ticker, bar_date, rp, above_trend FROM ml_features "
            "WHERE rp IS NOT NULL", conn)
        tgt = pd.read_sql(
            "SELECT ticker, bar_date, fwd_ret_20 FROM ml_targets "
            "WHERE fwd_ret_20 IS NOT NULL", conn)
    for df, col in ((trend, "date"), (rr, "signal_date"),
                    (px, "bar_date"), (feats, "bar_date"), (tgt, "bar_date")):
        df[col] = pd.to_datetime(df[col])
    px["c"] = px["c"].astype(float)
    return trend, rr, px, feats, tgt


def fwd20_from_px(px, ticker):
    s = (px[px.ticker == ticker].set_index("bar_date")["c"]
         .sort_index())
    return (s.shift(-20) / s - 1).rename("fwd_ret_20")


def range_pos(px, rr, instrument, und_ticker):
    """Position of the UNDERLYING close in the latest (<=5td old) range."""
    u = (px[px.ticker == und_ticker].set_index("bar_date")["c"]
         .sort_index().rename("u_close").reset_index())
    r = (rr[rr.ticker == instrument]
         .sort_values("signal_date")
         [["signal_date", "buy_trade", "sell_trade"]])
    if r.empty or u.empty:
        return None
    m = pd.merge_asof(u, r, left_on="bar_date", right_on="signal_date",
                      direction="backward",
                      tolerance=pd.Timedelta(days=7))
    lo = m["buy_trade"].astype(float)
    hi = m["sell_trade"].astype(float)
    pos = (m["u_close"] - lo) / (hi - lo)
    pos[hi <= lo] = np.nan
    return pd.DataFrame({"bar_date": m["bar_date"], "pos": pos})


def boot_ci(rule_hits, base_hits, rng):
    if len(rule_hits) < 20 or len(base_hits) < 20:
        return None
    d = (rng.choice(rule_hits, (NBOOT, len(rule_hits))).mean(axis=1)
         - rng.choice(base_hits, (NBOOT, len(base_hits))).mean(axis=1))
    return float(np.percentile(d, 5)), float(np.percentile(d, 95))


def build_frame(name, instrument, und, invert, has_mfr,
                trend, rr, px, feats, tgt, etf_for_fwd=None):
    """One row per bar inside the instrument's tag-coverage window."""
    etf = etf_for_fwd or name
    tg = trend[trend.ticker == instrument][["date", "tag"]]
    if tg.empty:
        return None
    t = tgt[tgt.ticker == etf][["bar_date", "fwd_ret_20"]]
    if t.empty:
        f20 = fwd20_from_px(px, etf).dropna().reset_index()
        t = f20[["bar_date", "fwd_ret_20"]]
    d = t.merge(tg.rename(columns={"date": "bar_date"}),
                on="bar_date", how="inner")     # coverage window = tag days
    d["fwd_ret_20"] = d["fwd_ret_20"].astype(float)
    want = "BEARISH" if invert else "BULLISH"
    d["bullish"] = d["tag"] == want
    if has_mfr:
        fe = feats[feats.ticker == etf][["bar_date", "rp", "above_trend"]]
        d = d.merge(fe, on="bar_date", how="left")
        d["lrr"] = ((d["above_trend"].astype(float) == 1)
                    & (d["rp"].astype(float) < 0.45))
    else:
        d["lrr"] = np.nan
    rp = range_pos(px, rr, instrument, und)
    if rp is not None:
        d = d.merge(rp, on="bar_date", how="left")
        d["cheap"] = ((1 - d["pos"]) if invert else d["pos"]) <= (1 / 3)
    else:
        d["cheap"] = np.nan
    if name in OWN_VOL:
        sym, thr = OWN_VOL[name]
        ov = (px[px.ticker == sym][["bar_date", "c"]]
              .rename(columns={"c": "ov"}))
        d = d.merge(ov, on="bar_date", how="left")
        d["hot"] = d["ov"].astype(float) > thr
    else:
        d["hot"] = np.nan
    return d


def stat_line(name, test, sub, base, rng, counters, flag=""):
    if len(sub) == 0:
        say(f"{name:<12} {test:<14} {'n=0':>44}  {flag}")
        return None
    rh = (sub["fwd_ret_20"] > 0).astype(float).to_numpy()
    bh = (base["fwd_ret_20"] > 0).astype(float).to_numpy()
    ci = boot_ci(rh, bh, rng)
    counters["tested"] += 1 if ci else 0
    if ci and ci[0] > 0:
        counters["positive"] += 1
    if ci and ci[1] < 0:
        counters["negative"] += 1
    ci_s = f"[{ci[0]*100:+5.1f},{ci[1]*100:+5.1f}]" if ci else "  thin  "
    star = " **" if ci and (ci[0] > 0 or ci[1] < 0) else ""
    thin = " (n<100)" if len(sub) < 100 else ""
    say(f"{name:<12} {test:<14} n={len(sub):>5} hit={rh.mean()*100:5.1f}% "
        f"ret={sub['fwd_ret_20'].mean()*100:+6.2f}%  vs base "
        f"{bh.mean()*100:5.1f}%  CI {ci_s}{star}{thin} {flag}")
    return rh


def main() -> int:
    rng = np.random.default_rng(11)
    trend, rr, px, feats, tgt = load_all()
    say(f"# T2 — Hedgeye direction x MFR timing ({date.today()})")
    say("\nBaseline (i) = all bars inside each instrument's tag-coverage "
        "window. Tag = same-day (mail lands pre-open, entry at close, "
        "staleness <=5td). Range position on the UNDERLYING's own close. "
        "TLT inverted (UST30Y is the yield). 90% bootstrap CIs of "
        "hit-rate difference vs (i).")

    counters = {"tested": 0, "positive": 0, "negative": 0}
    pooled: dict[str, list] = {k: [] for k in
                               ("base", "ii", "iii", "iv", "v")}

    say("\n## Part A — bridged tickers (MFR universe)")
    say(f"{'ticker':<12} {'test':<14} per-ticker stats")
    frames = {}
    for name, (instr, und, inv, mfr, flag) in BRIDGES.items():
        d = build_frame(name, instr, und, inv, mfr,
                        trend, rr, px, feats, tgt)
        if d is None or d.empty:
            say(f"{name:<12} NO TAG DATA {flag}")
            continue
        frames[name] = d
        base = d
        pooled["base"].append(d)
        say("")
        stat_line(name, "(i) all", base, base, rng,
                  {"tested": 0, "positive": 0, "negative": 0}, flag)
        sub2 = d[d["bullish"]]
        pooled["ii"].append(sub2)
        stat_line(name, "(ii) bull", sub2, base, rng, counters)
        if mfr:
            sub3 = d[d["bullish"] & (d["lrr"] == True)]  # noqa: E712
            pooled["iii"].append(sub3)
            stat_line(name, "(iii) bull+lrr", sub3, base, rng, counters)
        sub4 = d[d["bullish"] & (d["cheap"] == True)]  # noqa: E712
        pooled["iv"].append(sub4)
        stat_line(name, "(iv) bull+low3", sub4, base, rng, counters)
        if name in OWN_VOL:
            sub5 = d[d["bullish"] & (d["cheap"] == True)  # noqa: E712
                     & (d["hot"] == True)]  # noqa: E712
            pooled["v"].append(sub5)
            stat_line(name, "(v) +ownvol", sub5, base, rng, counters)

    # BRENT check for USO
    say("\n### USO cross-check with BRENT tags/ranges")
    dbr = build_frame("USO", "BRENT", "BZ=F", False, True,
                      trend, rr, px, feats, tgt, etf_for_fwd="USO")
    if dbr is not None and not dbr.empty:
        stat_line("USO(BRENT)", "(ii) bull", dbr[dbr["bullish"]], dbr,
                  rng, counters)
        stat_line("USO(BRENT)", "(iv) bull+low3",
                  dbr[dbr["bullish"] & (dbr["cheap"] == True)],  # noqa: E712
                  dbr, rng, counters)

    say("\n## Part B — tag-only (no MFR): megacaps + global/commodity")
    for name, (instr, pxsym) in TAG_ONLY.items():
        d = build_frame(name, instr, pxsym, False, False,
                        trend, rr, px, feats, tgt, etf_for_fwd=pxsym)
        if d is None or d.empty:
            say(f"{name:<12} NO TAG DATA")
            continue
        say("")
        stat_line(name, "(i) all", d, d, rng,
                  {"tested": 0, "positive": 0, "negative": 0})
        stat_line(name, "(ii) bull", d[d["bullish"]], d, rng, counters)
        stat_line(name, "(iv) bull+low3",
                  d[d["bullish"] & (d["cheap"] == True)],  # noqa: E712
                  d, rng, counters)
        pooled["base"].append(d)
        pooled["ii"].append(d[d["bullish"]])
        pooled["iv"].append(d[d["bullish"] & (d["cheap"] == True)])  # noqa: E712

    say("\n## Pooled (all tickers, Part A+B where the test applies)")
    bass = pd.concat(pooled["base"])
    for key, label in (("ii", "(ii) tag bullish"),
                       ("iii", "(iii) bullish+lrr"),
                       ("iv", "(iv) bullish+bottom-third"),
                       ("v", "(v) +own-vol hot")):
        subs = [s for s in pooled[key] if len(s)]
        if not subs:
            continue
        sub = pd.concat(subs)
        stat_line("POOLED", label, sub, bass, rng, counters)
    say("\nPooling caveat: bars across tickers share macro days — the "
        "bootstrap treats them as independent, so pooled CIs are "
        "anti-conservative. Per-ticker rows are the honest unit.")

    say("\n## Multiple-testing guard")
    say(f"CIs computed: {counters['tested']}; chance-expected one-sided "
        f"clears at 90% ~{counters['tested']*0.05:.1f}. Observed "
        f"{counters['positive']} positive / {counters['negative']} "
        f"negative excluding zero.")

    p = OUT / f"ml_round2_T2_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
