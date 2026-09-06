"""
price_backfill.py – TIER 2 setup: pull 2yr daily closes into price_history.

Run once (or nightly) before rs_corr.py. Safe to re-run; ON CONFLICT DO UPDATE
keeps the latest close for each (ticker, date) pair.

Usage:
    python -m tools.price_backfill          # uses DATABASE_URL from env
    python tools/price_backfill.py
"""
from __future__ import annotations
import os, datetime, logging
import yfinance as yf
import psycopg2

log = logging.getLogger("price_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

UNIVERSE = sorted([
    "HYG", "LQD", "SHY", "SPY", "TLT", "UUP",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
    "XLP", "XLRE", "XLU", "XLV", "XLY",
])
LOOKBACK_YEARS = 2
SOURCE = "yfinance"


def fetch_closes(ticker: str, start: datetime.date, end: datetime.date) -> list[tuple]:
    """Return [(date, close), ...] ascending."""
    df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                     auto_adjust=True, progress=False)
    if df.empty:
        log.warning("%s: no data returned", ticker)
        return []
    closes = []
    for idx, row in df.iterrows():
        d = idx.date() if hasattr(idx, "date") else idx
        c = float(row["Close"])
        if c > 0:
            closes.append((d, c))
    return closes


def upsert_closes(cur, ticker: str, closes: list[tuple]) -> int:
    rows = [(ticker, d, c, SOURCE) for d, c in closes]
    cur.executemany(
        """
        INSERT INTO price_history (ticker, d, close, source)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (ticker, d) DO UPDATE
            SET close = EXCLUDED.close,
                source = EXCLUDED.source,
                fetched_at = NOW()
        """,
        rows,
    )
    return len(rows)


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    end   = datetime.date.today()
    start = end.replace(year=end.year - LOOKBACK_YEARS)

    log.info("Backfilling %d tickers from %s to %s", len(UNIVERSE), start, end)

    conn = psycopg2.connect(db_url)
    try:
        with conn:
            cur = conn.cursor()
            total = 0
            for ticker in UNIVERSE:
                closes = fetch_closes(ticker, start, end)
                if not closes:
                    continue
                n = upsert_closes(cur, ticker, closes)
                total += n
                log.info("  %-6s  %d rows", ticker, n)
        log.info("Done. %d total rows upserted.", total)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
