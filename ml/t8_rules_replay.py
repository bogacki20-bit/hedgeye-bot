"""ml/t8_rules_replay.py — T8: replay Kris's 2026 clean LONG episodes
with rule-based exits. Same entries (actual buys, exactly as filled),
sells replaced by rules, evaluated daily on closes:

R1 TRIM      rp >= 0.9 (crossing) -> sell 50%. After any rule-sell,
             actual re-buys execute only if rp <= 0.35 AND trend intact
             (no synthetic re-buys).
R2 LADDER    range high < 3 days ago, 3 consecutive days -> sell 25%
             (once per streak). Close below TRADE -> reduce to starter
             (= first buy leg's shares). Close below TREND -> exit
             fully at the NEXT close.
R3 CLUSTER   any cluster (energy/oil one cluster; metals; crypto) over
             12% of AUM -> trim the largest cluster position to cap.
             AUM = snapshot series (before 2026-05-11 the 5/11 value —
             the Jan-2 statement is not in the DB; flagged).
R4 SPIKE     actual adds at rp > 0.8 or own-vol top band (USO:OVX>42,
             GLD/AAAU:GVZ>18) are dropped; count + dollars reported.

Signal sources per name (priority): HEDGEYE (bridged underlying's risk
range; trend intact = tag not BEARISH, TRADE = range low) -> MFR
(ml_features rp/above_trend/trade_dist/hi_d3) -> PROXY (range = 20d
high/low, TREND = SMA50, TRADE = 20d low), clearly labeled.

Per episode: actual P&L (T7 engine), RULES P&L, HOLD-to-9/4 P&L;
aggregates; capital-at-risk curve with a RULES (and HOLD) line added.

    py ml/t8_rules_replay.py
"""
from __future__ import annotations

import sqlite3
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
                             load_px, BRIDGE, INVERT, SQLITE, MTM_DATE)

OUT = REPO / "reports"
LINES: list[str] = []
CLUSTERS = {
    "energy_oil": {"USO", "BNO", "UGA", "XLE", "OIH", "XOP", "CRAK",
                   "AMLP", "BWET", "IEO", "PSCE", "FCG"},
    "metals": {"GLD", "AAAU", "SLV", "SIVR", "GDX", "GDXJ", "PPLT",
               "PALL", "CPER", "PHYS"},
    "crypto": {"IBIT", "ETHA", "BITO", "ETHE", "SETH", "BMNZ"},
}
OWN_VOL = {"USO": ("^OVX", 42.0), "GLD": ("^GVZ", 18.0),
           "AAAU": ("^GVZ", 18.0)}
CAP = 0.12


def say(s=""):
    print(s)
    LINES.append(s)


def load_signals():
    with db_pg.get_conn() as conn:
        rng = pd.read_sql(
            "SELECT ticker, signal_date, buy_trade, sell_trade FROM "
            "hedgeye_risk_ranges WHERE buy_trade IS NOT NULL "
            "AND sell_trade IS NOT NULL AND signal_date >= '2025-12-01'",
            conn)
        tag = pd.read_sql(
            "SELECT date, ticker, tag FROM hedgeye_trend_daily "
            "WHERE date >= '2025-12-01'", conn)
        mfr = pd.read_sql(
            "SELECT ticker, bar_date, rp, above_trend, trade_dist, hi_d3 "
            "FROM ml_features WHERE bar_date >= '2025-12-01'", conn)
    rng["signal_date"] = pd.to_datetime(rng["signal_date"])
    tag["date"] = pd.to_datetime(tag["date"])
    mfr["bar_date"] = pd.to_datetime(mfr["bar_date"])
    return rng, tag, mfr


def sig_frame(sym, cal, px, rng, tag, mfr, ovx, gvz):
    """Daily signal frame for one name: rp, hi, trend_ok, below_trade,
    below_trend, ownvol_hot, source."""
    c = px.get(sym)
    if c is None:
        return None
    c = c.reindex(cal).ffill()
    instr = BRIDGE.get(sym, sym)
    inv = sym in INVERT
    out = pd.DataFrame(index=cal)
    out["close"] = c

    r = rng[rng.ticker == instr]
    he_cov = 0.0
    if len(r):
        u = px.get({"GOLD": "GC=F", "WTIC": "CL=F", "USD": "DX-Y.NYB",
                    "SPX": "^GSPC", "COMPQ": "^IXIC", "RUT": "^RUT",
                    "UST30Y": "^TYX"}.get(instr, sym))
        u = (u if u is not None else c).reindex(cal).ffill()
        r = r.copy()
        r["signal_date"] = r["signal_date"].astype("datetime64[ns]")
        m = pd.merge_asof(
            pd.DataFrame({"d": pd.DatetimeIndex(cal).astype("datetime64[ns]")}),
            r.sort_values("signal_date"),
            left_on="d", right_on="signal_date", direction="backward",
            tolerance=pd.Timedelta(days=7))
        lo = m["buy_trade"].astype(float).values
        hi = m["sell_trade"].astype(float).values
        he_cov = np.mean(~np.isnan(lo))
        if he_cov >= 0.6:
            pos = (u.values - lo) / (hi - lo)
            if inv:
                pos = 1 - pos
            out["rp"] = pos
            out["hi"] = hi if not inv else -lo
            t = (tag[tag.ticker == instr].set_index("date")["tag"]
                 .reindex(cal).ffill())
            if inv:
                t = t.map({"BULLISH": "BEARISH", "BEARISH": "BULLISH",
                           "NEUTRAL": "NEUTRAL"})
            out["trend_ok"] = (t != "BEARISH") & t.notna()
            out["below_trend"] = t == "BEARISH"
            out["below_trade"] = ((u.values < lo) if not inv
                                  else (u.values > hi))
            out["source"] = "HEDGEYE"
    if "source" not in out.columns:
        f = mfr[mfr.ticker == sym].set_index("bar_date").reindex(cal)
        if f["rp"].notna().mean() >= 0.6:
            out["rp"] = f["rp"].astype(float)
            out["hi"] = np.nan
            out["hi_d3"] = f["hi_d3"].astype(float)
            out["trend_ok"] = f["above_trend"].astype(float) == 1
            out["below_trend"] = f["above_trend"].astype(float) == 0
            out["below_trade"] = f["trade_dist"].astype(float) < 0
            out["source"] = "MFR"
        else:
            hi20 = c.rolling(20, min_periods=10).max()
            lo20 = c.rolling(20, min_periods=10).min()
            sma50 = c.rolling(50, min_periods=25).mean()
            out["rp"] = (c - lo20) / (hi20 - lo20)
            out["hi"] = hi20
            out["trend_ok"] = c >= sma50
            out["below_trend"] = c < sma50
            out["below_trade"] = c < lo20
            out["source"] = "PROXY"
    if "hi_d3" not in out.columns:
        h = pd.Series(out["hi"].values, index=cal).astype(float)
        out["hi_d3"] = h - h.shift(3)
    ov = {"^OVX": ovx, "^GVZ": gvz}.get(OWN_VOL.get(sym, (None,))[0])
    thr = OWN_VOL.get(sym, (None, np.inf))[1]
    out["ownvol_hot"] = (ov.reindex(cal).ffill() > thr) if ov is not None \
        else False
    return out


def main() -> int:
    trades = load_trades()
    eps = build_episodes(trades)
    syms = sorted({e["symbol"] for e in eps})
    px = load_px(syms + ["SPY"])
    spy = px["SPY"].dropna()
    led = pd.DataFrame([episode_stats(e, px, spy) for e in eps])
    keep = (~led["pre_window"] & ~led["cusip"] & ~led["mismatch"]
            & ~led["no_px"] & ~led["short"] & led["ret"].notna())
    eps_clean = [e for e, k in zip(eps, keep) if k]
    led = led[keep].reset_index(drop=True)

    say(f"# T8 — rules-replay of Kris's 2026 long book ({date.today()})")
    rng, tag, mfr = load_signals()
    ovx = px.get("^OVX")
    gvz = px.get("^GVZ")
    if ovx is None or gvz is None:
        vol = load_px(["^OVX", "^GVZ"])
        ovx, gvz = vol.get("^OVX"), vol.get("^GVZ")
    cal = spy[(spy.index >= pd.Timestamp("2026-01-02"))
              & (spy.index <= MTM_DATE)].index

    conn = sqlite3.connect(SQLITE)
    aum = pd.read_sql_query(
        "SELECT snapshot_date, sum(current_value) v FROM portfolio_positions "
        "WHERE current_value IS NOT NULL GROUP BY snapshot_date", conn)
    conn.close()
    aum["snapshot_date"] = pd.to_datetime(aum["snapshot_date"])
    aum_s = (aum.set_index("snapshot_date")["v"].astype(float)
             .reindex(cal).ffill().bfill())   # bfill = May-11 AUM pre-May

    sigs, src_of = {}, {}
    for s in sorted({e["symbol"] for e in eps_clean}):
        sf = sig_frame(s, cal, px, rng, tag, mfr, ovx, gvz)
        if sf is not None:
            sigs[s] = sf
            src_of[s] = sf["source"].iloc[-1]
    n_cov = sum(1 for s in src_of.values() if s != "PROXY")
    d_cov = led[led["symbol"].map(src_of) != "PROXY"]["peak_inv"].sum()
    say(f"\n## Coverage: {n_cov}/{len(src_of)} names on real signals "
        f"(HEDGEYE {sum(1 for v in src_of.values() if v == 'HEDGEYE')}, "
        f"MFR {sum(1 for v in src_of.values() if v == 'MFR')}); "
        f"{(led['symbol'].map(src_of) != 'PROXY').mean()*100:.0f}% of "
        f"episodes, {d_cov / led['peak_inv'].sum()*100:.0f}% of episode "
        f"dollars. Rest on PROXY (20d range / SMA50), labeled.")

    # --- replay engine, day-synchronous for R3 ---
    state = []
    for i, e in enumerate(eps_clean):
        buys = sorted([(d, q, p, a) for d, q, p, a in e["legs"] if q > 0])
        state.append(dict(
            i=i, sym=e["symbol"], buys=buys, bi=0, sh=0.0, cash=0.0,
            invested=0.0, starter=buys[0][1] if buys else 0.0,
            trimmed=False, pend_exit=False, lh_armed=True, prev_rp=np.nan,
            done=False, r4_drop=0, r4_dollars=0.0))
    r4n = 0
    r4d = 0.0
    rules_book = pd.Series(0.0, index=cal)
    hold_book = pd.Series(0.0, index=cal)
    for d0 in cal:
        # per-episode rules
        for st in state:
            sym = st["sym"]
            sf = sigs.get(sym)
            if sf is None or d0 not in sf.index:
                continue
            row = sf.loc[d0]
            price = row["close"]
            if price != price:
                continue
            rp = row["rp"]
            # pending TREND exit executes at this close
            if st["pend_exit"] and st["sh"] > 0:
                st["cash"] += st["sh"] * price
                st["sh"] = 0.0
                st["pend_exit"] = False
            # actual buys today (gated)
            while st["bi"] < len(st["buys"]) and st["buys"][st["bi"]][0] <= d0:
                _d, q, p, a = st["buys"][st["bi"]]
                st["bi"] += 1
                blocked = (rp == rp and rp > 0.8) or bool(row["ownvol_hot"])
                if st["trimmed"] and not blocked:
                    blocked = not (rp == rp and rp <= 0.35
                                   and bool(row["trend_ok"]))
                if blocked:
                    st["r4_drop"] += 1
                    r4n += 1
                    r4d += -a
                else:
                    st["sh"] += q
                    st["cash"] += a
                    st["invested"] += -a
            if st["sh"] <= 0:
                st["prev_rp"] = rp
                continue
            # R1 trim on crossing
            if rp == rp and rp >= 0.9 and not (st["prev_rp"] >= 0.9):
                st["cash"] += 0.5 * st["sh"] * price
                st["sh"] *= 0.5
                st["trimmed"] = True
            # R2a lower highs (3 consecutive down-vs-3d)
            hd = sf["hi_d3"].loc[:d0].tail(3)
            if len(hd) == 3 and (hd < 0).all():
                if st["lh_armed"]:
                    st["cash"] += 0.25 * st["sh"] * price
                    st["sh"] *= 0.75
                    st["trimmed"] = True
                    st["lh_armed"] = False
            else:
                st["lh_armed"] = True
            # R2b below TRADE -> down to starter
            if bool(row["below_trade"]) and st["sh"] > st["starter"]:
                st["cash"] += (st["sh"] - st["starter"]) * price
                st["sh"] = st["starter"]
                st["trimmed"] = True
            # R2c below TREND -> exit next close
            if bool(row["below_trend"]):
                st["pend_exit"] = True
            st["prev_rp"] = rp
        # R3 cluster cap
        vals = {}
        for st in state:
            if st["sh"] > 0:
                sf = sigs.get(st["sym"])
                if sf is not None and d0 in sf.index:
                    pr = sf.loc[d0, "close"]
                    if pr == pr:
                        vals[st["i"]] = (st["sym"], st["sh"] * pr, pr)
        for cname, members in CLUSTERS.items():
            tot = sum(v for _i, (s, v, _p) in vals.items() if s in members)
            cap = CAP * float(aum_s.loc[d0])
            if tot > cap:
                over = tot - cap
                big = max(((i, v) for i, (s, v, _p) in vals.items()
                           if s in members), key=lambda kv: kv[1],
                          default=None)
                if big:
                    st = state[big[0]]
                    _s, v, pr = vals[big[0]]
                    sell_val = min(over, v)
                    st["cash"] += sell_val
                    st["sh"] -= sell_val / pr
                    st["trimmed"] = True
        # mark books
        for st in state:
            sf = sigs.get(st["sym"])
            if sf is not None and d0 in sf.index:
                pr = sf.loc[d0, "close"]
                if pr == pr:
                    rules_book[d0] += st["cash"] + st["sh"] * pr
    # HOLD book: all actual buys, no sells
    for e in eps_clean:
        c = px.get(e["symbol"])
        if c is None:
            continue
        buys = sorted([(d, q, p, a) for d, q, p, a in e["legs"] if q > 0])
        cc = c.reindex(cal).ffill()
        qty = cash = 0.0
        bi = 0
        for d0 in cal:
            while bi < len(buys) and buys[bi][0] <= d0:
                qty += buys[bi][1]
                cash += buys[bi][3]
                bi += 1
            if cc.loc[d0] == cc.loc[d0]:
                hold_book[d0] += cash + qty * cc.loc[d0]

    # per-episode rules P&L
    rows = []
    for st, (_, e7) in zip(state, led.iterrows()):
        sf = sigs.get(st["sym"])
        pr = (sf["close"].dropna().iloc[-1]
              if sf is not None and sf["close"].notna().any() else np.nan)
        pnl_rules = st["cash"] + st["sh"] * (pr if pr == pr else 0.0)
        c = px.get(st["sym"])
        hold_pnl = np.nan
        if c is not None:
            buys = st["buys"]
            last = c[c.index <= MTM_DATE]
            if len(last) and buys:
                q = sum(b[1] for b in buys)
                a = sum(b[3] for b in buys)
                hold_pnl = a + q * float(last.iloc[-1])
        rows.append(dict(sym=st["sym"], account=e7["account"],
                         open=e7["open"], src=src_of.get(st["sym"], "?"),
                         actual=e7["pnl"], rules=pnl_rules, hold=hold_pnl,
                         inv=e7["peak_inv"], r4=st["r4_drop"]))
    rl = pd.DataFrame(rows)

    say(f"\n## R4 spike gate: dropped {r4n} adds, {r4d:,.0f} USD "
        f"({r4n / max(1, sum(len(s['buys']) for s in state))*100:.0f}% of "
        f"all buy fills).")
    say("\n## Aggregates (long episodes only)")
    say(f"{'variant':<12}{'P&L $':>10}{'per $ invested':>16}")
    for k, lbl in (("actual", "ACTUAL"), ("rules", "RULES"),
                   ("hold", "HOLD-9/4")):
        v = rl[k].sum()
        say(f"{lbl:<12}{v:>+10,.0f}{v / rl['inv'].sum()*100:>15.1f}%")
    for src in ("HEDGEYE", "MFR", "PROXY"):
        sub = rl[rl["src"] == src]
        if len(sub):
            say(f"  {src:<10} n={len(sub):>3}  actual {sub['actual'].sum():+9,.0f}"
                f"  rules {sub['rules'].sum():+9,.0f}"
                f"  hold {sub['hold'].sum():+9,.0f}")

    say("\n## Per-episode (top 30 by |actual-rules| gap)")
    rl["gap"] = rl["rules"] - rl["actual"]
    say(f"{'sym':<7}{'src':<9}{'open':<12}{'actual$':>9}{'rules$':>9}"
        f"{'hold$':>9}{'gap$':>9}{'r4drops':>8}")
    for _, r in rl.reindex(rl["gap"].abs().sort_values(ascending=False)
                           .index[:30]).iterrows():
        say(f"{r['sym']:<7}{r['src']:<9}{str(r['open']):<12}"
            f"{r['actual']:>9,.0f}{r['rules']:>9,.0f}{r['hold']:>9,.0f}"
            f"{r['gap']:>9,.0f}{r['r4']:>8}")

    # curve: ACTUAL longs book + SPY + HOLD + RULES
    actual_book = pd.Series(0.0, index=cal)
    inv_cap = pd.Series(0.0, index=cal)
    for e in eps_clean:
        c = px.get(e["symbol"])
        if c is None:
            continue
        qty = cash = 0.0
        legs = iter(sorted(e["legs"]))
        nxt = next(legs, None)
        # realized P&L persists after close (cumulative curve, matching
        # the RULES/HOLD lines' accounting)
        cc = c.reindex(cal).ffill()
        for d0 in cal[cal >= e["open"]]:
            while nxt is not None and nxt[0] <= d0:
                _d, q, p, a = nxt
                qty += q
                cash += a
                nxt = next(legs, None)
            price = cc.loc[d0]
            if price != price:
                continue
            actual_book[d0] += cash + qty * price
            inv_cap[d0] += abs(qty) * price
    spy_ret = spy.reindex(cal).pct_change().fillna(0.0)
    spy_line = (inv_cap.shift(1).fillna(0.0) * spy_ret).cumsum()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(cal, actual_book.values, label="ACTUAL (longs)", lw=1.7)
    ax.plot(cal, spy_line.values, label="SPY same capital", lw=1.5)
    ax.plot(cal, hold_book.values, label="HOLD to 9/4", lw=1.5)
    ax.plot(cal, rules_book.values, label="RULES replay", lw=1.9)
    ax.axhline(0, color="gray", lw=0.6)
    ax.set_title("T8 — actual vs rules-replay vs hold (2026 long book)")
    ax.legend(); ax.grid(alpha=0.3)
    p1 = OUT / f"t8_rules_replay_{date.today().isoformat()}.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight"); plt.close(fig)
    say(f"\nPNG: {p1.name}. Final: ACTUAL {actual_book.iloc[-1]:+,.0f} | "
        f"RULES {rules_book.iloc[-1]:+,.0f} | HOLD {hold_book.iloc[-1]:+,.0f}"
        f" | SPY {spy_line.iloc[-1]:+,.0f} USD.")
    say("\nCaveats: longs only; sells at closes, no costs/slippage; R1 "
        "triggers on 0.9-crossings, R2a once per streak; starter = first "
        "buy leg's shares; AUM before 5/11 = the 5/11 snapshot (Jan-2 "
        "statement not in DB); PROXY names use 20d range / SMA50; "
        "dividends excluded.")

    p = OUT / f"kris_book_rules_replay_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
