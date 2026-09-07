"""ml/load_px_daily.py — populate px_daily (migration 089).

Five enrolled tickers from the round-1 TradingView CSVs (full OHLCV,
source='tradingview'); HYG and ^VIX daily history from yfinance with
auto_adjust=False (raw closes, source='yfinance-unadjusted'; ^VIX has no
volume). ^VIX is the index itself — the range-midpoint proxy the TrendSpider
scripts used is retired (Phase A brief).

    py ml/load_px_daily.py          # upsert everything
    py ml/load_px_daily.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402
from tradingview_ingest import TV_DIR, load_csv  # noqa: E402

TV_TICKERS = {
    "BATS_SPY_1D.csv": "SPY", "BATS_UUP_1D.csv": "UUP",
    "TVC_VIX_1D.csv": "^VIX_TV",  # kept for reference; ^VIX index comes from yfinance
    "BATS_USO_1D.csv": "USO", "BATS_AAAU_1D.csv": "AAAU",
    "BATS_TLT_1D.csv": "TLT",
}
YF_TICKERS = ["HYG", "^VIX"]
YF_PERIOD = "9y"


def tv_rows():
    out = []
    for fname, ticker in TV_TICKERS.items():
        rows, _body, _ = load_csv(TV_DIR / fname)
        for r in rows:
            out.append((ticker, r["date"], r["open"], r["high"], r["low"],
                        r["close"], r["vol"], "tradingview"))
    return out


def yf_rows():
    import yfinance as yf
    out = []
    df = yf.download(YF_TICKERS, period=YF_PERIOD, interval="1d",
                     group_by="ticker", auto_adjust=False, progress=False,
                     threads=True)
    for t in YF_TICKERS:
        sub = df[t] if len(YF_TICKERS) > 1 else df
        for idx, row in sub.iterrows():
            c = float(row["Close"])
            if c != c:
                continue
            f = lambda k: (float(row[k]) if row[k] == row[k] else None)
            v = f("Volume")
            out.append((t, idx.date(), f("Open"), f("High"), f("Low"), c,
                        v if v else None, "yfinance-unadjusted"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rows = tv_rows() + yf_rows()
    per = {}
    for r in rows:
        per.setdefault(r[0], [0, None, None])
        per[r[0]][0] += 1
        per[r[0]][1] = min(per[r[0]][1] or r[1], r[1])
        per[r[0]][2] = max(per[r[0]][2] or r[1], r[1])
    for t, (n, lo, hi) in sorted(per.items()):
        print(f"  {t:<8} {n:>5} bars  {lo} .. {hi}")
    if args.dry_run:
        print(f"[dry-run] {len(rows)} rows, no writes")
        return 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), 1000):
            execute_values(
                cur,
                """INSERT INTO px_daily (ticker, bar_date, o, h, l, c, v, source)
                   VALUES %s
                   ON CONFLICT (ticker, bar_date) DO UPDATE SET
                       o=EXCLUDED.o, h=EXCLUDED.h, l=EXCLUDED.l,
                       c=EXCLUDED.c, v=EXCLUDED.v, source=EXCLUDED.source,
                       loaded_at=now()""",
                rows[i:i + 1000], page_size=1000)
            conn.commit()
    print(f"upserted {len(rows)} rows into px_daily")
    return 0


if __name__ == "__main__":
    sys.exit(main())
