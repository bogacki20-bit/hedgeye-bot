"""tools/mfr_correlations.py - nightly MFR correlation reads (Kris 2026-11-15 "Yes, add it").

Tracking only: nothing here is on the card. About 10 MCP requests per run, no ticker cost
(every ticker must already be active in MFR). Writes four tables (migration 138):
  mfr_sector_corr  AVG_SECTOR_CORR on SPY, 10 and 21 bars (high = market moves as one block)
  mfr_pair_corr    mfr_get_today pairs: GLD/SPY, SPY/UUP, GLD/UUP, SPY/TLT (10 and 21 day)
  mfr_book_corr    each book name vs SPY/UUP/TLT/GLD/USO, 30 and 90 trading days
  mfr_book_pairs   every pair among the book's 15 largest names, 30 and 90 days
Summary view: v_mfr_book_corr_summary (market-value-weighted book correlation per day).

Usage: py -m tools.mfr_correlations [--bars 5] [--dry-run]   (--bars 250 = one-year sector backfill)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

log = logging.getLogger("mfr_correlations")
BENCH = ["SPY", "UUP", "TLT", "GLD", "USO"]
WINDOWS = [30, 90]
TOP_N = 15
EXCLUDE = ["SPAXX", "FDRXX", "CORE", "BUXX", "Pending activity"]

BOOK_SQL = """
SELECT symbol, sum(market_value) FROM book_positions
WHERE snapshot_date = %s AND NOT coalesce(is_option, false)
  AND coalesce(asset_class, '') NOT ILIKE '%%cash%%' AND position('*' in symbol) = 0
  AND symbol <> ALL(%s)
GROUP BY symbol HAVING sum(market_value) <> 0
ORDER BY abs(sum(market_value)) DESC
"""

UPSERT_SECTOR = """
INSERT INTO mfr_sector_corr (bar_date, avg10, avg21) VALUES (%s, %s, %s)
ON CONFLICT (bar_date) DO UPDATE SET avg10 = EXCLUDED.avg10, avg21 = EXCLUDED.avg21, fetched_at = now()
"""

UPSERT_PAIR = """
INSERT INTO mfr_pair_corr (as_of, a, b, c10, c21, trend) VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (as_of, a, b) DO UPDATE SET c10 = EXCLUDED.c10, c21 = EXCLUDED.c21,
  trend = EXCLUDED.trend, fetched_at = now()
"""

BOOK_COLS = [f"{b.lower()}_{w}" for b in BENCH for w in WINDOWS]
UPSERT_BOOK = (
    "INSERT INTO mfr_book_corr (as_of, ticker, book_date, market_value, " + ", ".join(BOOK_COLS) + ") "
    "VALUES (" + ", ".join(["%s"] * (4 + len(BOOK_COLS))) + ") "
    "ON CONFLICT (as_of, ticker) DO UPDATE SET book_date = EXCLUDED.book_date, market_value = EXCLUDED.market_value, "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in BOOK_COLS) + ", fetched_at = now()"
)

UPSERT_BOOK_PAIR = """
INSERT INTO mfr_book_pairs (as_of, win, a, b, corr) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (as_of, win, a, b) DO UPDATE SET corr = EXCLUDED.corr, fetched_at = now()
"""


def _call(cli, name, args, tries=4):
    from tools.mfr_indicators import RateLimited
    for _ in range(tries):
        try:
            r = cli.tool(name, args)
        except RateLimited as e:
            log.warning("%s rate limited, sleeping %ss", name, e.wait)
            time.sleep(min(e.wait + 5, 900))
            continue
        if isinstance(r, dict) and r.get("_error"):
            raise RuntimeError(f"{name}: {r['_error'].get('message')}")
        return r
    raise RuntimeError(f"{name}: still rate limited after {tries} tries")


def book(cur):
    cur.execute("SELECT max(snapshot_date) FROM book_positions")
    snap = cur.fetchone()[0]
    cur.execute(BOOK_SQL, (snap, EXCLUDE))
    return snap, [(s, float(v)) for s, v in cur.fetchall()]


def sector_series(cli, period, bars):
    r = _call(cli, "mfr_get_indicator", {"ticker": "SPY", "indicators": ["AVG_SECTOR_CORR"], "timeframe": "D",
                                         "bars": bars, "params": {"AVG_SECTOR_CORR": {"period": period}}})
    ind = (r.get("indicators") or [{}])[0]
    s = ind.get("series") or {}
    out = {d: v for d, v in zip(s.get("time", []), s.get("avg", [])) if v is not None}
    if not out and ind.get("lastBar"):
        out[ind["lastBar"]] = (ind.get("values") or {}).get("avg")
    return out


def run(bars: int = 5, dry_run: bool = False) -> dict:
    import db_pg
    import psycopg2
    from tools.mfr_indicators import McpClient, MAX_BARS
    db_pg._load_dotenv_fallback()
    tok = os.environ.get("MFR_MCP_TOKEN")
    if not tok:
        raise RuntimeError("MFR_MCP_TOKEN not set")
    bars = max(1, min(int(bars), MAX_BARS))
    cli = McpClient(tok)
    cli.init()
    conn = psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn())
    cur = conn.cursor()
    st = {"sector_rows": 0, "pairs": 0, "book_names": 0, "book_pairs": 0, "missing": []}

    # 1. sector correlation to SPY, 10 and 21 bars
    s10, s21 = sector_series(cli, 10, bars), sector_series(cli, 21, bars)
    for d in sorted(set(s10) | set(s21)):
        if not dry_run:
            cur.execute(UPSERT_SECTOR, (d, s10.get(d), s21.get(d)))
        st["sector_rows"] += 1
    if s10:
        last = max(s10)
        st["sector_last"] = [last, round(s10[last], 3), round(s21.get(last) or 0, 3)]

    # 2. Today page pair correlations
    t = _call(cli, "mfr_get_today", {})
    corr = t.get("correlations") or {}
    as_of_today = corr.get("asOf") or t.get("as_of")
    for p in corr.get("pairs", []):
        if not dry_run:
            cur.execute(UPSERT_PAIR, (as_of_today, p.get("a"), p.get("b"), p.get("c10"), p.get("c21"), p.get("trend")))
        st["pairs"] += 1

    # 3. book vs benchmarks
    snap, bk = book(cur)
    mv = dict(bk)
    names = [s for s, _ in bk]
    seen = set()
    for i in range(0, len(names), 25):
        r = _call(cli, "mfr_get_correlation_to_benchmarks",
                  {"tickers": names[i:i + 25], "benchmarks": BENCH, "windows": WINDOWS})
        as_of = (r.get("method") or {}).get("evaluated_at") or as_of_today
        for row in r.get("rows", []):
            tk = row.get("ticker")
            seen.add(tk)
            vals = [row.get(f"{b}_{w}") for b in BENCH for w in WINDOWS]
            if not dry_run:
                cur.execute(UPSERT_BOOK, (as_of, tk, snap, mv.get(tk), *vals))
            st["book_names"] += 1
    st["missing"] = [n for n in names if n not in seen]

    # 4. pairs among the 15 largest covered names
    top = [n for n in names if n in seen][:TOP_N]
    for w in WINDOWS:
        if len(top) < 2:
            break
        r = _call(cli, "mfr_get_correlation_matrix", {"tickers": top, "window": w})
        as_of = (r.get("method") or {}).get("evaluated_at") or as_of_today
        for a, b, c in r.get("pairs", []):
            if not dry_run:
                cur.execute(UPSERT_BOOK_PAIR, (as_of, w, a, b, c))
            st["book_pairs"] += 1
    if not dry_run:
        conn.commit()
    conn.close()
    st["book_date"] = str(snap)
    st["requests"] = cli.n
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run(bars=a.bars, dry_run=a.dry_run), default=str, indent=1))


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
