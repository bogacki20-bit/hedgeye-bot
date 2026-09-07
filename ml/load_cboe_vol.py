"""ml/load_cboe_vol.py — Phase B(c): CBOE vol-index daily history
(roadmap section 3 + operator brief 2026-09-07).

VIX3M, VVIX, SKEW, OVX (oil vol), GVZ (gold vol), COR1M, COR3M.
Primary source: CBOE CDN daily-prices CSVs; fallback yfinance (^SYM,
auto_adjust=False); an index missing from both is SKIPPED LOUDLY.
Stored in px_daily as ^SYM with source 'cboe' / 'yfinance-unadjusted';
raw CSVs banked in data/reference/cboe/.

    py ml/load_cboe_vol.py [--dry-run]
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

REF = REPO / "data" / "reference" / "cboe"
CDN = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"
SYMS = ["VIX3M", "VVIX", "SKEW", "OVX", "GVZ", "COR1M", "COR3M"]


def fetch_cboe(sym):
    import requests
    r = requests.get(CDN.format(sym=sym), timeout=60)
    if r.status_code != 200:
        return None
    REF.mkdir(parents=True, exist_ok=True)
    (REF / f"{sym}_History.csv").write_text(r.text, encoding="utf-8")
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip().upper() for c in df.columns]
    df["DATE"] = pd.to_datetime(df["DATE"])
    # two CDN layouts: OHLC (VIX3M/COR*) or a single value column named
    # after the index (VVIX/SKEW/OVX/GVZ) — normalize to CLOSE
    if "CLOSE" not in df.columns and sym.upper() in df.columns:
        df = df.rename(columns={sym.upper(): "CLOSE"})
    return df


def fetch_yf(sym):
    import yfinance as yf
    df = yf.download(f"^{sym}", period="10y", interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        lvl = 0 if "Close" in df.columns.get_level_values(0) else -1
        df.columns = df.columns.get_level_values(lvl)
    out = df.reset_index()[["Date", "Open", "High", "Low", "Close"]]
    out.columns = ["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rows = []
    for sym in SYMS:
        src = "cboe"
        df = None
        try:
            df = fetch_cboe(sym)
        except Exception as e:
            print(f"  {sym}: CDN error {e}")
        if df is None:
            try:
                df = fetch_yf(sym)
                src = "yfinance-unadjusted"
            except Exception as e:
                print(f"  {sym}: yfinance error {e}")
        if df is None:
            print(f"MISSING: {sym} — not on the CBOE CDN or yfinance; skipped LOUDLY")
            continue
        n = 0
        for _, r in df.iterrows():
            c = pd.to_numeric(r.get("CLOSE"), errors="coerce")
            if c != c or c is None:
                continue
            g = lambda k: (lambda v: None if v != v else float(v))(
                pd.to_numeric(r.get(k), errors="coerce"))
            rows.append((f"^{sym}", r["DATE"].date(), g("OPEN"), g("HIGH"),
                         g("LOW"), float(c), None, src))
            n += 1
        print(f"  ^{sym:<7} {n:>5} bars  {df['DATE'].min().date()} .. "
              f"{df['DATE'].max().date()}  [{src}]")
    if args.dry_run:
        print(f"[dry-run] {len(rows)} rows")
        return 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), 1000):
            execute_values(
                cur,
                "INSERT INTO px_daily (ticker, bar_date, o, h, l, c, v, source) "
                "VALUES %s ON CONFLICT (ticker, bar_date) DO UPDATE SET "
                "o=EXCLUDED.o, h=EXCLUDED.h, l=EXCLUDED.l, c=EXCLUDED.c, "
                "source=EXCLUDED.source, loaded_at=now()",
                rows[i:i + 1000], page_size=1000)
            conn.commit()
    print(f"upserted {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
