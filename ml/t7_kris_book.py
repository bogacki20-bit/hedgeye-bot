"""ml/t7_kris_book.py — T7: Kris's ACTUAL realized P&L vs buy-and-hold
of the same names over the same dates.

Source: the bot's SQLite broker ledger (C:/data/hedgeye.db,
portfolio_transactions from Fidelity Accounts_History / History_for_
Account exports; positions snapshots for cross-checks). Trades only
(YOU BOUGHT / YOU SOLD); options legs (symbols like -AMZN260717P230)
are a separate flagged bucket; reverse-split CUSIP symbols flagged
ambiguous. Episodes per (account, symbol): open when cumulative qty
leaves 0, close when it returns; every fill is a leg. An episode whose
first fill is a SELL in a long-only account predates the data window ->
flagged 'pre-window', excluded from clean comparisons.

Counterfactuals per episode (unadjusted closes, dividends excluded on
BOTH sides): (a) full size at first entry, hold to exit (uses actual
first-entry fill and actual exit fills); (b) hold to 2026-09-04;
(c) SPY over the same dates. Episode return = P&L / peak invested.
Splits: Hedgeye tag bullish at entry / in Re-Rank at entry / entry in
the bottom third of Keith's range (bridged underlyings, TLT inverted).
Small n by design — a dated ledger, no CIs.

    py ml/t7_kris_book.py
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()

SQLITE = r"C:\data\hedgeye.db"
MTM_DATE = pd.Timestamp("2026-09-04")
OUT = REPO / "reports"
CACHE = REPO / "data" / "reference" / "t7_px_unadj.csv"
LINES: list[str] = []

BRIDGE = {"AAAU": "GOLD", "GLD": "GOLD", "USO": "WTIC", "UUP": "USD",
          "TLT": "UST30Y", "SPY": "SPX", "QQQ": "COMPQ", "IWM": "RUT"}
INVERT = {"TLT"}


def say(s=""):
    print(s)
    LINES.append(s)


def load_trades():
    conn = sqlite3.connect(SQLITE)
    df = pd.read_sql_query(
        "SELECT run_date, account, symbol, quantity, price, amount, action "
        "FROM portfolio_transactions WHERE (upper(action) LIKE '%BOUGHT%' "
        "OR upper(action) LIKE '%SOLD%') AND symbol IS NOT NULL "
        "AND symbol != ''", conn)
    conn.close()
    df["run_date"] = pd.to_datetime(df["run_date"], format="%m/%d/%Y")
    df["account"] = df["account"].replace(
        {"X96383748": "Individual",
         "Cash Management (Individual)": "Individual"})
    df["is_option"] = df["symbol"].str.startswith("-")
    df["is_cusip"] = (~df["is_option"] & (df["symbol"].str.len() > 5)
                      & df["symbol"].str.contains(r"\d"))
    for c in ("quantity", "price", "amount"):
        df[c] = df[c].astype(float)
    return df.sort_values(["account", "symbol", "run_date"]).reset_index(drop=True)


def load_px(tickers):
    import yfinance as yf
    px = (pd.read_csv(CACHE, index_col=0, parse_dates=True)
          if CACHE.exists() else pd.DataFrame())
    need = [t for t in tickers if t not in px.columns]
    for i in range(0, len(need), 50):
        batch = need[i:i + 50]
        df = yf.download(batch, period="14mo", interval="1d",
                         auto_adjust=False, progress=False, threads=True)
        close = df["Close"] if "Close" in df.columns.get_level_values(0) else df
        if isinstance(close, pd.Series):
            close = close.to_frame(batch[0])
        for t in batch:
            if t in close.columns and close[t].dropna().shape[0] > 20:
                px[t] = close[t].dropna()
    if need:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        px.to_csv(CACHE)
    return px.sort_index()


def build_episodes(trades):
    eps = []
    for (acct, sym), g in trades[~trades["is_option"]].groupby(
            ["account", "symbol"]):
        cum = 0.0
        cur = None
        for _, r in g.iterrows():
            pre = cum
            cum += r["quantity"]
            if cur is None:
                cur = {"account": acct, "symbol": sym, "legs": [],
                       "open": r["run_date"], "pre_window": r["quantity"] < 0
                       and acct != "Individual", "cusip": bool(r["is_cusip"]),
                       "first_px": r["price"],
                       "first_qty": abs(r["quantity"])}
            cur["legs"].append((r["run_date"], r["quantity"], r["price"],
                                r["amount"]))
            if abs(cum) < 1e-4 and abs(pre) >= 1e-4:
                cur["close"] = r["run_date"]
                cur["resid"] = 0.0
                eps.append(cur)
                cur = None
                cum = 0.0
        if cur is not None:
            cur["close"] = None
            cur["resid"] = cum
            eps.append(cur)
    return eps


def episode_stats(ep, px, spy):
    legs = ep["legs"]
    # split/bad-fill sanity: any fill >20% away from that day's close means
    # the qty path is corrupted (reverse split mid-episode) or data is bad
    mism = False
    c0 = px.get(ep["symbol"])
    if c0 is not None:
        for d0, _q, p0, _a in legs:
            cd = c0[c0.index <= d0]
            if len(cd) and p0 > 0:
                r0 = p0 / float(cd.iloc[-1])
                if r0 > 1.2 or r0 < 0.8:
                    mism = True
                    break
    ep = {**ep, "mismatch": mism}
    buys = [(d, q, p, a) for d, q, p, a in legs if q > 0]
    sells = [(d, q, p, a) for d, q, p, a in legs if q < 0]
    short = bool(sells) and (not buys or sells[0][0] < buys[0][0])
    gross_buy = sum(-a for _, _, _, a in buys)
    gross_sell = sum(a for _, _, _, a in sells)
    qty_b = sum(q for _, q, _, _ in buys)
    qty_s = -sum(q for _, q, _, _ in sells)
    avg_in = gross_buy / qty_b if qty_b else np.nan
    avg_out = gross_sell / qty_s if qty_s else np.nan
    sym = ep["symbol"]
    c = px.get(sym)
    open_d, close_d = ep["open"], ep["close"]
    # daily position path for invested capital + drawdown
    end = close_d or MTM_DATE
    pnl = gross_sell - gross_buy
    mtm_px = np.nan
    if close_d is None and c is not None and len(c[c.index <= MTM_DATE]):
        mtm_px = float(c[c.index <= MTM_DATE].iloc[-1])
        pnl += ep["resid"] * mtm_px
    # peak invested + episode dd from daily marks
    peak_inv = dd = np.nan
    if c is not None:
        cal = c[(c.index >= open_d) & (c.index <= end)]
        qty = 0.0
        cash = 0.0
        path, inv = [], []
        legs_i = iter(sorted(legs))
        nxt = next(legs_i, None)
        costq = 0.0
        for d0, price in cal.items():
            while nxt is not None and nxt[0] <= d0:
                _d, q, p, a = nxt
                qty += q
                cash += a
                costq += (-a if q > 0 else 0)
                nxt = next(legs_i, None)
            path.append(cash + qty * price)
            inv.append(abs(costq))
        if inv and max(inv) > 0:
            peak_inv = max(inv)
            eqs = np.array(path) / peak_inv
            dd = float((eqs - np.maximum.accumulate(
                np.maximum(eqs, 0))).min())
    if peak_inv != peak_inv or peak_inv == 0:
        peak_inv = gross_buy or gross_sell or np.nan
    ret = pnl / peak_inv if peak_inv and peak_inv == peak_inv else np.nan
    # counterfactuals
    f_px = ep["first_px"]
    exit_px = avg_out if close_d is not None else mtm_px
    if short:
        ret_a = (f_px - (avg_in if close_d is not None else mtm_px)) / f_px \
            if f_px else np.nan
    else:
        ret_a = (exit_px / f_px - 1) if f_px and exit_px == exit_px else np.nan
    ret_b = np.nan
    if c is not None and len(c[c.index <= MTM_DATE]):
        last = float(c[c.index <= MTM_DATE].iloc[-1])
        ret_b = (last / f_px - 1) if not short else (f_px - last) / f_px
    ret_c = np.nan
    s0 = spy[spy.index >= open_d]
    s1 = spy[spy.index <= (close_d or MTM_DATE)]
    if len(s0) and len(s1):
        ret_c = float(s1.iloc[-1] / s0.iloc[0] - 1)
    hold = ((close_d or MTM_DATE) - open_d).days
    return dict(account=ep["account"], symbol=sym, short=short,
                open=open_d.date(), close=close_d.date() if close_d else None,
                legs=len(legs), avg_in=avg_in, avg_out=avg_out,
                pnl=pnl, peak_inv=peak_inv, ret=ret, dd=dd, hold=hold,
                ret_a=ret_a, ret_b=ret_b, ret_c=ret_c,
                pre_window=ep["pre_window"], cusip=ep["cusip"],
                mismatch=ep["mismatch"], no_px=c is None)


def load_hedgeye():
    with db_pg.get_conn() as conn:
        trend = pd.read_sql("SELECT date, ticker, tag FROM "
                            "hedgeye_trend_daily", conn)
        rrank = pd.read_sql("SELECT note_date, ticker FROM hedgeye_rerank "
                            "WHERE rank IS NOT NULL", conn)
        ranges = pd.read_sql(
            "SELECT ticker, signal_date, buy_trade, sell_trade FROM "
            "hedgeye_risk_ranges WHERE buy_trade IS NOT NULL", conn)
    trend["date"] = pd.to_datetime(trend["date"])
    rrank["note_date"] = pd.to_datetime(rrank["note_date"])
    ranges["signal_date"] = pd.to_datetime(ranges["signal_date"])
    return trend, rrank, ranges


def splits_for(led, trend, rrank, ranges, px):
    tags, inre, low3 = [], [], []
    for _, e in led.iterrows():
        sym = e["symbol"]
        d0 = pd.Timestamp(e["open"])
        instr = BRIDGE.get(sym, sym)
        t = trend[(trend.ticker == instr) & (trend.date == d0)]
        tag = t["tag"].iloc[0] if len(t) else None
        if tag is not None and sym in INVERT:
            tag = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}.get(tag, tag)
        tags.append(tag)
        ri = rrank[(rrank.note_date <= d0) & (rrank.ticker == sym)]
        last_issue = rrank[rrank.note_date <= d0]["note_date"].max()
        inre.append(bool(len(ri[ri.note_date == last_issue]))
                    if pd.notna(last_issue) else None)
        rg = ranges[(ranges.ticker == instr)
                    & (ranges.signal_date <= d0)
                    & (ranges.signal_date >= d0 - pd.Timedelta(days=7))]
        v = None
        c = px.get(sym)
        if len(rg) and c is not None:
            rg = rg.sort_values("signal_date").iloc[-1]
            lo, hi = float(rg["buy_trade"]), float(rg["sell_trade"])
            cc = c[c.index <= d0]
            if hi > lo and len(cc):
                pos = (float(cc.iloc[-1]) - lo) / (hi - lo)
                if sym in INVERT:
                    pos = 1 - pos
                v = pos <= 1 / 3
        low3.append(v)
    led["tag_bull"] = [t == "BULLISH" if t else None for t in tags]
    led["in_rerank"] = inre
    led["entry_low3"] = low3
    return led


def agg_line(name, sub):
    if not len(sub):
        say(f"{name:<34} n=0")
        return
    dw = sub["pnl"].sum() / sub["peak_inv"].sum() * 100
    ew = sub["ret"].mean() * 100
    hit = (sub["ret"] > 0).mean() * 100
    va = (sub["ret"] - sub["ret_a"]).mean() * 100
    vc = (sub["ret"] - sub["ret_c"]).mean() * 100
    say(f"{name:<34} n={len(sub):>3} $wt={dw:+6.1f}% eq={ew:+6.1f}% "
        f"hit={hit:4.0f}% vs(a){va:+6.1f}pts vs SPY{vc:+6.1f}pts")


def main() -> int:
    trades = load_trades()
    say(f"# T7 — Kris's book vs hold ({date.today()})")
    say("\n## Coverage (honest first)")
    say(f"Broker data: {trades['run_date'].min().date()} .. "
        f"{trades['run_date'].max().date()} — 8 months, 2026 only. "
        f"{len(trades)} trade fills, "
        f"{trades[~trades['is_option']]['symbol'].nunique()} equity/ETF "
        f"symbols, {trades['is_option'].sum()} option fills "
        f"({trades[trades['is_option']]['symbol'].nunique()} contracts) — "
        f"options are a flagged side bucket. MTM at 2026-09-04.")
    opt_pnl = trades[trades["is_option"]]["amount"].sum()
    say(f"Options bucket net cash flow: {opt_pnl:+,.0f} USD "
        f"(premium collected/paid net of closes; no MTM on open contracts).")

    eps = build_episodes(trades)
    syms = sorted({e["symbol"] for e in eps})
    px = load_px(syms + ["SPY"])
    spy = px["SPY"].dropna()
    led = pd.DataFrame([episode_stats(e, px, spy) for e in eps])
    clean = led[~led["pre_window"] & ~led["cusip"] & ~led["mismatch"]
                & ~led["no_px"]
                & led["ret"].notna() & led["ret_a"].notna()
                & led["ret_c"].notna()]
    closed = clean[clean["close"].notna()]
    openp = clean[clean["close"].isna()]
    mm = led[led["mismatch"]]
    say(f"\nEpisodes: {len(led)} total -> {len(clean)} clean "
        f"({len(closed)} closed, {len(openp)} open at 9/4); excluded: "
        f"{led['pre_window'].sum()} pre-window, {led['cusip'].sum()} "
        f"CUSIP-renamed, {led['mismatch'].sum()} fill-vs-close mismatch "
        f">20% (reverse splits / corrupted qty paths: "
        f"{', '.join(sorted(mm['symbol'].unique())[:14])}), "
        f"{led['no_px'].sum()} no price data. Mismatch-bucket net P&L "
        f"by cash flow only: {mm['pnl'].sum():+,.0f} USD (UNRELIABLE — "
        f"marks corrupted; ambiguous per brief).")
    gold_energy = openp[openp["symbol"].isin(
        ["AAAU", "GLD", "PHYS", "GDX", "GDXJ", "SLV", "SIVR", "XLE", "OIH",
         "USO", "BNO", "UGA", "CPER", "CRAK", "XOP", "AMLP", "BWET"])]
    say(f"Open positions P&L: {openp['pnl'].sum():+,.0f} USD of which "
        f"gold/energy/commodity names {gold_energy['pnl'].sum():+,.0f} USD "
        f"({', '.join(sorted(gold_energy['symbol'].unique()))}).")
    say(f"Total book P&L (clean episodes): {clean['pnl'].sum():+,.0f} USD "
        f"on peak invested {clean['peak_inv'].sum():,.0f} USD-episodes.")

    trend, rrank, ranges = load_hedgeye()
    clean = splits_for(clean.copy(), trend, rrank, ranges, px)

    say("\n## Aggregates (episode return = P&L / peak invested; "
        "vs(a) = actual minus full-size-at-first-entry-hold-to-exit; "
        "vs SPY = actual minus SPY same dates)")
    agg_line("ALL clean", clean)
    agg_line("closed only", closed.merge(
        clean[["account", "symbol", "open"]], how="inner"))
    agg_line("open only", clean[clean["close"].isna()])
    for acct in clean["account"].unique():
        agg_line(f"  account: {acct}", clean[clean["account"] == acct])
    say("")
    agg_line("longs", clean[~clean["short"]])
    agg_line("shorts", clean[clean["short"]])
    say("")
    agg_line("Hedgeye tag BULLISH at entry", clean[clean["tag_bull"] == True])  # noqa: E712
    agg_line("tag not-bullish/none", clean[clean["tag_bull"] != True])  # noqa: E712
    agg_line("in Re-Rank at entry", clean[clean["in_rerank"] == True])  # noqa: E712
    agg_line("not in Re-Rank", clean[clean["in_rerank"] != True])  # noqa: E712
    agg_line("entry in bottom third of range", clean[clean["entry_low3"] == True])  # noqa: E712
    agg_line("entry NOT bottom third", clean[clean["entry_low3"] == False])  # noqa: E712

    say("\n## Ledger — every clean episode (sorted by |P&L|)")
    say(f"{'acct':<12}{'sym':<7}{'S':<2}{'open':<11}{'close':<11}"
        f"{'legs':>4}{'avg_in':>9}{'avg_out':>9}{'hold':>5}"
        f"{'P&L$':>9}{'ret':>7}{'maxDD':>7}{'(a)':>7}{'(b)':>7}{'SPY':>7}")
    for _, e in clean.reindex(
            clean["pnl"].abs().sort_values(ascending=False).index).iterrows():
        say(f"{e['account'][:11]:<12}{e['symbol']:<7}"
            f"{'S' if e['short'] else 'L':<2}{str(e['open']):<11}"
            f"{str(e['close'] or 'OPEN'):<11}{e['legs']:>4}"
            f"{e['avg_in']:>9.2f}"
            f"{(e['avg_out'] if e['avg_out'] == e['avg_out'] else np.nan):>9.2f}"
            f"{e['hold']:>5}{e['pnl']:>9.0f}{e['ret']*100:>6.1f}%"
            f"{(e['dd']*100 if e['dd'] == e['dd'] else np.nan):>6.1f}%"
            f"{e['ret_a']*100:>6.1f}%{e['ret_b']*100:>6.1f}%"
            f"{e['ret_c']*100:>6.1f}%")

    # cumulative realized P&L vs SPY on the same capital-at-risk
    cal = spy[(spy.index >= trades["run_date"].min())
              & (spy.index <= MTM_DATE)].index
    inv = pd.Series(0.0, index=cal)
    book = pd.Series(0.0, index=cal)
    bad = {(r["account"], r["symbol"], r["open"]) for _, r in
           led[led["mismatch"] | led["cusip"] | led["pre_window"]].iterrows()}
    for e in eps:
        c = px.get(e["symbol"])
        if c is None or (e["account"], e["symbol"], e["open"].date()) in bad:
            continue
        end = e["close"] or MTM_DATE
        qty = cash = costq = 0.0
        legs = iter(sorted(e["legs"]))
        nxt = next(legs, None)
        sub = c[(c.index >= e["open"]) & (c.index <= end)].reindex(cal).dropna()
        for d0, price in sub.items():
            while nxt is not None and nxt[0] <= d0:
                _d, q, p, a = nxt
                qty += q
                cash += a
                costq += (-a if q > 0 else abs(a) if qty < 0 else 0)
                nxt = next(legs, None)
            book[d0] += cash + qty * price
            inv[d0] += abs(qty) * price
    spy_ret = spy.reindex(cal).pct_change().fillna(0.0)
    spy_pnl = (inv.shift(1).fillna(0.0) * spy_ret).cumsum()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(book.index, book.values, label="Kris book P&L (all episodes)",
            lw=1.7)
    ax.plot(spy_pnl.index, spy_pnl.values,
            label="SPY on same capital-at-risk", lw=1.7)
    ax.axhline(0, color="gray", lw=0.6)
    ax.set_title("T7 — cumulative P&L vs SPY on the same capital at risk")
    ax.legend(); ax.grid(alpha=0.3)
    p1 = OUT / f"t7_book_vs_spy_{date.today().isoformat()}.png"
    fig.savefig(p1, dpi=120, bbox_inches="tight"); plt.close(fig)
    say(f"\nPNG: {p1.name}. Final: book {book.iloc[-1]:+,.0f} USD vs SPY "
        f"{spy_pnl.iloc[-1]:+,.0f} USD on the same daily capital.")
    say("\nCaveats: dividends excluded on both sides (unadjusted closes); "
        "episode return = P&L / peak invested; options bucket cash-flow "
        "only; pre-window and reverse-split episodes excluded (flagged "
        "above); split fields None when no bridgeable Hedgeye instrument.")

    p = OUT / f"kris_book_vs_hold_{date.today().isoformat()}.md"
    p.write_text("\n".join(LINES), encoding="utf-8")
    print(f"\nwrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
