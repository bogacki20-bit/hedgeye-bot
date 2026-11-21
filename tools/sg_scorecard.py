"""tools/sg_scorecard.py - score SpotGamma's signals before any of them enters the process (Kris 11/15/2026: "find the
best way to incorporate SpotGamma in our process" -> "Yes, run it"). Tracking only; nothing here is on the card.

Four tests on the history we already hold:
  1. Index regime (SPX, SPY, NDX, QQQ, RUT, IWM, daily since 2018, from the Indices pages' level history):
     price below SpotGamma's Volatility Trigger, and net gamma by quintile -> next-day move size, next-day and
     5-day return, next-5-day realized vol.
  2. Index walls: does price cross the call wall (or break the put wall) within 5 sessions less often than an
     ordinary level the same distance away would be crossed? (excess < 0 = the wall held more than chance)
  3. Equity Hub call wall (spotgamma_snapshots, ~4,900 names, since 6/20): distance to the call wall vs the next
     ~week's return, rank IC per date; also inside our Hedgeye inventory (book, Signal Strength, ETF Pro).
  4. Tape flow (sg_tape, SPX complex, 6/15-10/23): premium-weighted aggressor flow (buy calls + sell puts = +)
     vs the next day's and next 5 days' SPX return.
  Plus gamma tilt (sg_tilt) vs the next day's move size.

    railway run py -m tools.sg_scorecard [--out data/reviews/sg_scorecard_<date>.md]
Read-only on the database. Index history comes from the newest data/sg_pages/raw/*/sg_page_daily_*.json.gz
(full 2018+ series); falls back to sg_series (last 400 sessions).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

YF = {"SPX": "^GSPC", "SPY": "SPY", "NDX": "^NDX", "QQQ": "QQQ", "RUT": "^RUT", "IWM": "IWM"}


def _conn():
    import db_pg
    import psycopg2
    db_pg._load_dotenv_fallback()
    return psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn())


# ---------- 1-2. index levels ----------
_REF: dict = {}


def _own_prices(sym: str, df: pd.DataFrame) -> bool:
    """11/20: until the daily run's Indices fix, symbols after the first in a tab load were saved with another index's
    data (QQQ/NDX/RUT as SPY, IWM as RUT). Keep a history only if its last price matches sym's Home Index Levels."""
    from tools.sg_pages import _ref_upx, _scale_ok
    if "upx" not in df or not len(df) or df["upx"].iloc[-1] is None:
        return True
    if sym not in _REF:
        with _conn() as c:
            _REF[sym] = _ref_upx(c.cursor(), sym)
    if _REF[sym] is None or _scale_ok(float(df["upx"].iloc[-1]), _REF[sym]):
        return True
    print(f"sg_scorecard: {sym} level history carries another index's prices ({float(df['upx'].iloc[-1]):,.0f}); skipped")
    return False


def load_level_history() -> dict[str, pd.DataFrame]:
    out = {}
    files = sorted(glob.glob(os.path.join("data", "sg_pages", "raw", "*", "sg_page_daily_*.json.gz")))
    if files:
        with gzip.open(files[-1], "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        for cap in d.get("captures", []):
            sym = cap.get("sym")
            if cap.get("page") != "indices" or not sym or sym in out:
                continue
            for v in cap.get("states", {}).values():
                if isinstance(v, list) and v and isinstance(v[0], dict) and {"max_g_strike", "call_wall_strike", "net_gamma"} <= set(v[0]):
                    df = pd.DataFrame([x for x in v if isinstance(x, dict)])
                    if _own_prices(sym, df):
                        out[sym] = df
                    break
    if len(out) < 6:   # fallback: the table keeps the last 400 sessions
        with _conn() as c:
            q = pd.read_sql("SELECT DISTINCT ON (sym) sym, data FROM sg_series WHERE name='levels_history' AND page LIKE 'indices%%' "
                            "ORDER BY sym, captured_date DESC", c)
        for _, r in q.iterrows():
            df = pd.DataFrame(r["data"])
            if r["sym"] not in out and _own_prices(r["sym"], df):
                out[r["sym"]] = df
    for s, df in out.items():
        df["date"] = pd.to_datetime(df["trade_date"].str[:10])
        out[s] = df.drop_duplicates("date").set_index("date").sort_index()
    return out


def closes(tickers: list[str], start: str) -> pd.DataFrame:
    import yfinance as yf
    px = yf.download(tickers, start=start, auto_adjust=True, progress=False)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    px.index = pd.to_datetime(px.index).tz_localize(None)
    return px


def index_tests(hist: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = min(df.index.min() for df in hist.values()).strftime("%Y-%m-%d")
    px = closes(list(YF.values()), start)
    regime, walls = [], []
    for sym, df in hist.items():
        c = px[YF[sym]].dropna()
        r = c.pct_change()
        f1 = r.shift(-1)
        f5 = c.shift(-5) / c - 1
        rv5 = r[::-1].rolling(5).std()[::-1].shift(-1) * np.sqrt(252)       # realized vol of the next 5 sessions
        mx5 = c[::-1].rolling(5).max()[::-1].shift(-1) / c - 1               # best close within the next 5 sessions
        mn5 = c[::-1].rolling(5).min()[::-1].shift(-1) / c - 1
        g = df.join(pd.DataFrame({"close": c, "f1": f1, "f5": f5, "rv5": rv5, "mx5": mx5, "mn5": mn5}), how="inner").dropna(subset=["f1"])
        if g.empty:
            continue
        # SpotGamma's levels are quoted on its own price (upx); compare the same-day ratio, not the raw level
        g["below_vt"] = g["upx"] < g["max_g_strike"]
        g["gq"] = pd.qcut(g["net_gamma"].rank(method="first"), 5, labels=["Q1 most neg", "Q2", "Q3", "Q4", "Q5 most pos"])
        def row(label, s):
            return {"sym": sym, "bucket": label, "n": len(s), "abs_f1%": round(100 * s["f1"].abs().mean(), 2),
                    "f1%": round(100 * s["f1"].mean(), 3), "f5%": round(100 * s["f5"].mean(), 2),
                    "up5%": round(100 * (s["f5"] > 0).mean(), 1), "rv5%": round(100 * s["rv5"].mean(), 1)}
        regime.append(row("ALL", g))
        regime.append(row("below vol trigger", g[g["below_vt"]]))
        regime.append(row("above vol trigger", g[~g["below_vt"]]))
        for q, s in g.groupby("gq", observed=True):
            regime.append(row(f"net gamma {q}", s))
        # walls: call wall 0-3% above price / put wall 0-3% below
        dcw = g["call_wall_strike"] / g["upx"] - 1
        dpw = 1 - g["put_wall_strike"] / g["upx"]
        allmx = g["mx5"].values
        allmn = -g["mn5"].values
        cw = g[(dcw > 0) & (dcw <= 0.03)]
        pw = g[(dpw > 0) & (dpw <= 0.03)]
        if len(cw) > 20:
            act = float((cw["mx5"] >= dcw[cw.index]).mean())
            exp = float(np.mean([(allmx >= d).mean() for d in dcw[cw.index]]))
            walls.append({"sym": sym, "wall": "call wall (cap)", "n": len(cw), "crossed%": round(100 * act, 1),
                          "chance%": round(100 * exp, 1), "excess_pp": round(100 * (act - exp), 1)})
        if len(pw) > 20:
            act = float((-pw["mn5"] >= dpw[pw.index]).mean())
            exp = float(np.mean([(allmn >= d).mean() for d in dpw[pw.index]]))
            walls.append({"sym": sym, "wall": "put wall (floor)", "n": len(pw), "crossed%": round(100 * act, 1),
                          "chance%": round(100 * exp, 1), "excess_pp": round(100 * (act - exp), 1)})
    return pd.DataFrame(regime), pd.DataFrame(walls)


# ---------- 3. Equity Hub call wall ----------
def equity_test() -> pd.DataFrame:
    with _conn() as c:
        s = pd.read_sql("SELECT ticker, snapshot_date, price, call_wall, put_wall FROM spotgamma_snapshots "
                        "WHERE capture_type='equityhub_csv' AND price > 1 AND call_wall > 0", c)
        inv = pd.read_sql("SELECT DISTINCT symbol AS t FROM book_positions UNION SELECT DISTINCT ticker FROM hedgeye_signal_strength "
                          "UNION SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges", c)
    inv = set(inv["t"])
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"])
    s = s.astype({"price": float, "call_wall": float, "put_wall": float})
    wide = s.pivot_table(index="snapshot_date", columns="ticker", values="price")
    dates = list(wide.index)
    rows = []
    last_used = None
    for i, d in enumerate(dates):
        fut = [x for x in dates[i + 1:] if 6 <= (x - d).days <= 12]
        if not fut:
            continue
        nonover = last_used is None or (d - last_used).days >= 7
        f = fut[0]
        cur = s[s["snapshot_date"] == d].set_index("ticker")
        ret = (wide.loc[f] / wide.loc[d] - 1).rename("fwd")
        x = cur.join(ret, how="inner").dropna(subset=["fwd"])
        x = x[(x["fwd"].abs() < 0.5)]
        x["dcw"] = x["call_wall"] / x["price"] - 1
        x["dpw"] = 1 - x["put_wall"] / x["price"]
        for scope, y in (("all", x), ("hedgeye inventory", x[x.index.isin(inv)])):
            if len(y) < 30:
                continue
            ic = y["dcw"].rank().corr(y["fwd"].rank())
            icp = y["dpw"].rank().corr(y["fwd"].rank())
            q = pd.qcut(y["dcw"].rank(method="first"), 5, labels=False)
            spread = y.loc[q == 4, "fwd"].mean() - y.loc[q == 0, "fwd"].mean()
            rows.append({"date": d.date(), "fwd_to": f.date(), "scope": scope, "n": len(y), "ic_callwall": ic,
                         "ic_putwall": icp, "far_minus_near%": 100 * spread, "nonoverlap": nonover})
        if nonover:
            last_used = d
    return pd.DataFrame(rows)


# ---------- 4. tape flow ----------
def tape_test() -> pd.DataFrame:
    with _conn() as c:
        t = pd.read_sql("SELECT (fetched_at AT TIME ZONE 'America/New_York')::date AS d, symbol, side, buysell, cp, premium "
                        "FROM sg_tape WHERE symbol IN ('SPX','SPY','XSP') OR symbol LIKE '/ES%%'", c)
    t["sign"] = np.where((t["buysell"] == "BUY") == (t["cp"] == "CALL"), 1, -1)   # buy call / sell put = +
    t["agg"] = t["side"].isin(["ASK", "BID"])                                     # traded at the touch = aggressive
    day = t.groupby("d").apply(lambda g: pd.Series({
        "prints": len(g), "prem": g["premium"].sum(),
        "flow": (g["premium"] * g["sign"]).sum() / max(g["premium"].sum(), 1),
        "aggr_flow": (g.loc[g["agg"], "premium"] * g.loc[g["agg"], "sign"]).sum() / max(g.loc[g["agg"], "premium"].sum(), 1),
        "put_share": g.loc[g["cp"] == "PUT", "premium"].sum() / max(g["premium"].sum(), 1)}), include_groups=False)
    day.index = pd.to_datetime(day.index)
    px = closes(["^GSPC"], (day.index.min() - pd.Timedelta(days=5)).strftime("%Y-%m-%d"))["^GSPC"]
    r = px.pct_change()
    day = day.join(pd.DataFrame({"f1": r.shift(-1), "f5": px.shift(-5) / px - 1, "same": r}), how="inner").dropna()
    return day[day["prints"] >= 50]


def tilt_test() -> pd.DataFrame:
    with _conn() as c:
        t = pd.read_sql("SELECT sym, trade_date, gamma_tilt, delta_tilt FROM sg_tilt", c)
    t["trade_date"] = pd.to_datetime(t["trade_date"])
    px = closes(["^GSPC", "QQQ", "IWM"], (t["trade_date"].min() - pd.Timedelta(days=5)).strftime("%Y-%m-%d"))
    m = {"SPX": "^GSPC", "QQQ": "QQQ", "IWM": "IWM"}
    rows = []
    for sym, g in t.groupby("sym"):
        if sym not in m:
            continue
        c = px[m[sym]].dropna()
        r = c.pct_change()
        # trade_date's tilt is published with the next session's note (data-desk-routes), so score the session after
        nxt = pd.DataFrame({"next_abs": r.shift(-1).abs(), "next": r.shift(-1)})
        x = g.set_index("trade_date").join(nxt, how="inner").dropna()
        if len(x) < 15:
            continue
        rows.append({"sym": sym, "n": len(x), "corr(tilt, next |move|)": round(x["gamma_tilt"].corr(x["next_abs"]), 3),
                     "corr(tilt, next move)": round(x["gamma_tilt"].corr(x["next"]), 3),
                     "corr(delta tilt, next move)": round(x["delta_tilt"].corr(x["next"]), 3)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    today = dt.date.today().isoformat()
    out = a.out or f"data/reviews/sg_scorecard_{today}.md"
    L = [f"# SpotGamma scorecard ({today})", "",
         "Tracking only (Kris 11/15): nothing here is on the card. Returns are close to close.", ""]

    hist = load_level_history()
    reg, walls = index_tests(hist)
    span = ", ".join(f"{s} {df.index.min():%Y-%m-%d}..{df.index.max():%Y-%m-%d}" for s, df in hist.items())
    L += ["## 1. Index regime: Volatility Trigger and net gamma", "", f"History: {span}.",
          "abs_f1 = average size of the next day's move; f1 / f5 = average next-day / 5-day return; up5 = share of "
          "5-day returns above zero; rv5 = realized vol over the next 5 sessions (annualized).", "",
          "~~~", reg.to_string(index=False), "~~~", ""]
    L += ["## 2. Index walls: do they hold more than an ordinary level?", "",
          "Days with the call wall within 3% above price (or the put wall within 3% below). crossed = price closed "
          "through it within 5 sessions; chance = how often a level at the same distance gets crossed on any day. "
          "excess < 0 means the wall held more than chance.", "", "~~~", walls.to_string(index=False), "~~~", ""]

    eq = equity_test()
    L += ["## 3. Equity Hub: distance to the call wall vs the next week's return", ""]
    if not eq.empty:
        for scope in ("all", "hedgeye inventory"):
            e = eq[eq["scope"] == scope]
            en = e[e["nonoverlap"]]
            if e.empty:
                continue
            L.append(f"- {scope}: {len(e)} dates (non-overlapping {len(en)}), avg names {e['n'].mean():.0f}. "
                     f"Call-wall IC mean {e['ic_callwall'].mean():+.3f} (non-overlapping {en['ic_callwall'].mean():+.3f}, "
                     f"t {en['ic_callwall'].mean() / (en['ic_callwall'].std(ddof=1) / np.sqrt(len(en))):+.1f}); "
                     f"far-minus-near quintile {e['far_minus_near%'].mean():+.2f}%/wk. "
                     f"Put-wall IC mean {e['ic_putwall'].mean():+.3f}.")
        L += ["", "IC < 0 = names far below their call wall did worse (the October finding); the probation test "
              "needs the non-overlapping call-wall IC at -0.05 or lower.", "",
              "~~~", eq.round(3).to_string(index=False), "~~~", ""]

    tp = tape_test()
    L += ["## 4. Tape flow (SPX complex, old capture 6/15-10/23)", ""]
    if not tp.empty:
        for col in ("flow", "aggr_flow", "put_share"):
            L.append(f"- {col}: corr with next-day SPX {tp[col].corr(tp['f1']):+.3f}, with next 5 days "
                     f"{tp[col].corr(tp['f5']):+.3f}, with the same day {tp[col].corr(tp['same']):+.3f} (n {len(tp)} days)")
        tp["t"] = pd.qcut(tp["aggr_flow"].rank(method="first"), 3, labels=["bearish third", "middle", "bullish third"])
        tt = tp.groupby("t", observed=True)[["f1", "f5"]].mean().mul(100).round(2)
        L += ["", "flow = premium-weighted (buy calls + sell puts) minus (buy puts + sell calls), as a share of premium; "
              "aggr_flow uses only trades at the bid/ask. The capture was a snapshot of the grid each run, not every "
              "print, so this is a sample.", "", "~~~", tt.to_string(), "~~~", ""]

    tl = tilt_test()
    L += ["## 5. Gamma tilt vs the next session", "", "~~~", tl.to_string(index=False) if not tl.empty else "no data", "~~~", ""]

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    print("wrote", out)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
