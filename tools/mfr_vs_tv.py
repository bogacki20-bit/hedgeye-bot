"""tools/mfr_vs_tv.py - compare MFR's Trade/Trend lines with the bot's TradingView lines.

For every ticker that has both a TradingView row (v_screener tv_trade / level_lines cloud) and an
MFR indicator row for the same bar, report how far apart they are. Read-only.
Usage: py -m tools.mfr_vs_tv [--out data/mfr/mfr_vs_tv_<date>.md]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    a = ap.parse_args()
    import db_pg
    import psycopg2
    db_pg._load_dotenv_fallback()
    conn = psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn())
    tv = pd.read_sql("SELECT ticker, price, tv_trade, tv_trend FROM v_screener WHERE tv_trade IS NOT NULL", conn)
    mfr = pd.read_sql("SELECT ticker, bar_date, vola_trade, vola_trend, trend_bull, trend_bear FROM v_mfr_indicators_latest", conn)
    conn.close()
    m = tv.merge(mfr, on="ticker", how="inner")
    for c in ("price", "tv_trade", "vola_trade"):
        m[c] = m[c].astype(float)
    m["trade_diff_pct"] = 100 * (m["vola_trade"] - m["tv_trade"]) / m["tv_trade"]
    m["abs"] = m["trade_diff_pct"].abs()
    n = len(m)
    within01 = int((m["abs"] <= 0.1).sum())
    within1 = int((m["abs"] <= 1.0).sum())
    worst = m.sort_values("abs", ascending=False).head(25)
    out = a.out or f"data/mfr/mfr_vs_tv_{date.today().isoformat()}.md"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# MFR vs TradingView Trade line ({date.today().isoformat()})\n\n")
        f.write(f"Names with both: {n}. Within 0.1%: {within01} ({100*within01/max(n,1):.1f}%). "
                f"Within 1%: {within1} ({100*within1/max(n,1):.1f}%).\n\n")
        f.write("Largest gaps (a TV row older than the MFR bar shows up here too):\n\n")
        f.write(worst[["ticker", "bar_date", "price", "tv_trade", "vola_trade", "trade_diff_pct"]]
                .round(3).to_string(index=False))
        f.write("\n")
    print(f"both={n} within0.1%={within01} within1%={within1}; wrote {out}")
    print(worst[["ticker", "bar_date", "tv_trade", "vola_trade", "trade_diff_pct"]].head(10).round(3).to_string(index=False))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
