"""tools/mfr_signal_scorecard.py - score MFR platform signals before any of them enters the process.

Kris 2026-11-15: track the MFR indicators and see how good they are before using them.
Reads mfr_indicators (tools/mfr_indicators.py), pulls daily closes from yfinance, and for each
event type measures the forward 5 / 10 / 21-day return:
  hit%  = share of events where price moved the signal's way (up for buy-type, down for sell-type)
  avg   = mean forward return in the signal's direction
  edge  = avg minus the same tickers' average forward return on all days (the base rate)
Split by trend state on the event day: above the MFR Trend band / inside / below.

Events (flag = 1 on the bar, flips = state change vs the prior bar):
  bs_buy, bs_mega_buy (buy-type) | bs_sell, bs_mega_sell (sell-type) | btfd_buy (buy-type)
  vola_calm   : vola_bear 1 -> 0 (Vola2vola turns calm)       buy-type
  vola_stress : vola_bear 0 -> 1 (Vola2vola turns stressed)   sell-type
  mx_wvf_hot  : Matrix volatility spike                        buy-type (capitulation read)
  mom1_low / mom1_high : short momentum p1 <= -2 / >= +2       buy-type / sell-type (stretched)
  cross_up_tm / cross_dn_tm : close crosses the monthly pivot  buy-type / sell-type

Usage: py -m tools.mfr_signal_scorecard [--since 2024-11-01] [--out data/reviews/mfr_signals_<date>.md]
Read-only on the database. Futures (_F) have no indicators and are skipped.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import pandas as pd

HORIZONS = (5, 10, 21)
BUY = {"bs_buy", "bs_mega_buy", "btfd_buy", "vola_calm", "mx_wvf_hot", "mom1_low", "cross_up_tm"}
SELL = {"bs_sell", "bs_mega_sell", "vola_stress", "mom1_high", "cross_dn_tm"}


def load_indicators(since: str) -> pd.DataFrame:
    import db_pg
    import psycopg2
    db_pg._load_dotenv_fallback()
    conn = psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn())
    df = pd.read_sql(
        "SELECT ticker, bar_date, vola_bear, trend_bull, trend_bear, tm_open, bs_buy, bs_mega_buy, "
        "bs_sell, bs_mega_sell, btfd_buy, mx_wvf_hot, mom_p1 FROM mfr_indicators "
        "WHERE bar_date >= %s AND ticker NOT LIKE '%%\\_F' ORDER BY ticker, bar_date", conn, params=[since])
    conn.close()
    df["bar_date"] = pd.to_datetime(df["bar_date"])
    return df


def load_closes(tickers: list[str], since: str) -> pd.DataFrame:
    import yfinance as yf
    frames = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        px = yf.download(chunk, start=since, auto_adjust=True, progress=False, group_by="column", threads=True)
        if px is None or px.empty:
            continue
        close = px["Close"] if "Close" in px else px
        if isinstance(close, pd.Series):
            close = close.to_frame(chunk[0])
        frames.append(close)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out


def build(df: pd.DataFrame, closes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tk, g in df.groupby("ticker"):
        if tk not in closes.columns:
            continue
        c = closes[tk].dropna()
        if len(c) < 40:
            continue
        g = g.set_index("bar_date").sort_index()
        g = g.join(c.rename("close"), how="inner")
        if g.empty:
            continue
        for h in HORIZONS:
            fwd = c.shift(-h) / c - 1
            g[f"f{h}"] = fwd.reindex(g.index)
        g["trend_state"] = "inside"
        g.loc[g["close"] > g["trend_bull"], "trend_state"] = "above"
        g.loc[g["close"] < g["trend_bear"], "trend_state"] = "below"
        prev_bear = g["vola_bear"].shift(1)
        prev_close, prev_tm = g["close"].shift(1), g["tm_open"].shift(1)
        ev = {
            "bs_buy": g["bs_buy"] == 1, "bs_mega_buy": g["bs_mega_buy"] == 1,
            "bs_sell": g["bs_sell"] == 1, "bs_mega_sell": g["bs_mega_sell"] == 1,
            "btfd_buy": g["btfd_buy"] == 1, "mx_wvf_hot": g["mx_wvf_hot"] == 1,
            "vola_calm": (prev_bear == 1) & (g["vola_bear"] == 0),
            "vola_stress": (prev_bear == 0) & (g["vola_bear"] == 1),
            "mom1_low": g["mom_p1"] <= -2, "mom1_high": g["mom_p1"] >= 2,
            "cross_up_tm": (prev_close <= prev_tm) & (g["close"] > g["tm_open"]),
            "cross_dn_tm": (prev_close >= prev_tm) & (g["close"] < g["tm_open"]),
        }
        base = g[[f"f{h}" for h in HORIZONS]].mean()
        for name, mask in ev.items():
            sub = g[mask.fillna(False)]
            for d, r in sub.iterrows():
                rec = {"ticker": tk, "date": d, "event": name, "trend_state": r["trend_state"]}
                for h in HORIZONS:
                    rec[f"f{h}"] = r[f"f{h}"]
                    rec[f"b{h}"] = base[f"f{h}"]
                rows.append(rec)
    return pd.DataFrame(rows)


def summarize(ev: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (name, state), g in list(ev.groupby(["event", "trend_state"])) + [((n, "ALL"), g) for n, g in ev.groupby("event")]:
        sign = 1 if name in BUY else -1
        rec = {"event": name, "trend": state, "n": len(g)}
        for h in HORIZONS:
            f = g[f"f{h}"].dropna()
            b = g.loc[f.index, f"b{h}"]
            if len(f) == 0:
                continue
            rec[f"hit{h}"] = round(100 * float(((sign * f) > 0).mean()), 1)
            rec[f"avg{h}"] = round(100 * float((sign * f).mean()), 2)
            rec[f"edge{h}"] = round(100 * float((sign * (f - b)).mean()), 2)
        out.append(rec)
    s = pd.DataFrame(out)
    order = {"ALL": 0, "above": 1, "inside": 2, "below": 3}
    return s.sort_values(["event", "trend"], key=lambda c: c.map(order) if c.name == "trend" else c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-11-01")
    ap.add_argument("--out")
    a = ap.parse_args()
    df = load_indicators(a.since)
    if df.empty:
        print("mfr_indicators is empty; run tools.mfr_indicators first")
        return
    tickers = sorted(df["ticker"].unique())
    closes = load_closes(tickers, a.since)
    ev = build(df, closes)
    if ev.empty:
        print("no events")
        return
    s = summarize(ev)
    out = a.out or f"data/reviews/mfr_signals_{date.today().isoformat()}.md"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# MFR signal scorecard ({date.today().isoformat()})\n\n")
        f.write(f"Bars since {a.since}; {len(tickers)} tickers with indicators, {closes.shape[1]} with closes; "
                f"{len(ev)} events. Tracking only (Kris 11/15): nothing here is on the card.\n\n")
        f.write("hit% = moved the signal's way; avg = mean move the signal's way; edge = avg minus the same "
                "tickers' all-day base rate. Horizons in trading days. trend = vs the MFR Trend band on the event day.\n\n")
        f.write("~~~\n" + s.to_string(index=False) + "\n~~~")
        f.write("\n")
    print(s.to_string(index=False))
    print("wrote", out)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
