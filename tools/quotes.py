"""Fresh Yahoo quotes for the whole screener universe -> live_quotes.

MARKET ticket 9/25: single names printed MFR's once-a-day price (fetched ~9:35)
for the rest of the session. Every price the bot prints now comes from here:
v_screener.price takes the NEWER of the live Yahoo quote and MFR's snapshot
(migration 117), and range_pos is recomputed from it.

    refresh(tickers=None, run_id=None) -> {ticker: {price, quote_at}}   batched, persisted
    py -m tools.quotes                 # refresh every v_screener ticker
    py -m tools.quotes XLE CAVA CL_F   # a few

Batching: one yf.download per BATCH symbols (1-minute bars, last print wins).
A symbol that fails keeps its previous live_quotes row, but the run's
quote_snapshots row is written with price NULL, so callers can mark it stale.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import re
import uuid

log = logging.getLogger(__name__)
BATCH = 150

# Yahoo futures roots that exist as <ROOT>=F (Eurex contracts don't).
YF_FUTURES = {"ES", "NQ", "RTY", "YM", "CL", "NG", "HO", "RB", "PL", "PA", "GC", "SI",
              "HG", "ZB", "ZN", "ZF", "ZT", "UB", "ZQ", "ZC", "ZS", "ZW", "ZL", "ZM",
              "6E", "VX"}


def yahoo_symbol(ticker: str) -> str | None:
    t = (ticker or "").strip().upper()
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE
        if t in HEDGEYE_TO_YFINANCE:
            return HEDGEYE_TO_YFINANCE[t]
    except Exception:
        pass
    m = re.fullmatch(r"([0-9A-Z]+)_F", t)
    if m:
        return f"{m.group(1)}=F" if m.group(1) in YF_FUTURES else None
    m = re.fullmatch(r"([A-Z]{3})/([A-Z]{3})", t)
    if m:
        return f"{m.group(1)}{m.group(2)}=X"
    m = re.fullmatch(r"([A-Z]{2,5})USD", t)
    if m and t not in {"FXUSD"}:
        return f"{m.group(1)}-USD"
    return t


def _universe() -> list[str]:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM v_screener")
        return [r[0] for r in cur.fetchall()]


def _download(symbols: list[str]) -> dict:
    """{yahoo_symbol: (price, quote_at_utc)} from 1-minute bars (last print)."""
    import pandas as pd
    import yfinance as yf
    out: dict = {}
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        try:
            df = yf.download(chunk, period="5d", interval="1m", group_by="ticker",
                             auto_adjust=False, prepost=False, threads=True, progress=False)
        except Exception as e:  # noqa: BLE001
            log.warning("quotes: batch %d failed: %s", i // BATCH, e)
            continue
        for s in chunk:
            try:
                col = df[s]["Close"] if len(chunk) > 1 else df["Close"]
                col = col.dropna()
                if len(col):
                    ts = col.index[-1]
                    ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
                    out[s] = (float(col.iloc[-1]), ts.to_pydatetime())
            except (KeyError, IndexError, TypeError):
                pass
    return out


def refresh(tickers: list[str] | None = None, run_id: str | None = None) -> dict:
    """Fetch + persist. Returns {ticker: {'price', 'quote_at'}} for successes."""
    import db_pg
    tickers = tickers or _universe()
    run_id = run_id or f"q-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    ymap = {t: yahoo_symbol(t) for t in tickers}
    got = _download(sorted({y for y in ymap.values() if y}))
    polled = dt.datetime.now(dt.timezone.utc)
    result = {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for t, y in ymap.items():
            q = got.get(y) if y else None
            cur.execute(
                "INSERT INTO quote_snapshots (run_id, ticker, price, fetched_at, quote_at) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id, ticker) DO NOTHING",
                (run_id, t, q[0] if q else None, polled, q[1] if q else None))
            if q:
                cur.execute(
                    "INSERT INTO live_quotes (ticker, price, fetched_at, polled_at, source) "
                    "VALUES (%s,%s,%s,%s,'yahoo') ON CONFLICT (ticker) DO UPDATE SET "
                    "price=EXCLUDED.price, fetched_at=EXCLUDED.fetched_at, polled_at=EXCLUDED.polled_at, "
                    "source=EXCLUDED.source WHERE live_quotes.fetched_at <= EXCLUDED.fetched_at",
                    (t, q[0], q[1], polled))
                result[t] = {"price": q[0], "quote_at": q[1]}
        conn.commit()
    log.info("quotes %s: %d/%d priced", run_id, len(result), len(tickers))
    return {"run_id": run_id, "quotes": result, "missed": sorted(set(tickers) - set(result))}


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import db_pg
    db_pg._load_dotenv_fallback()
    r = refresh([t.upper() for t in a.tickers] or None)
    print(f"{r['run_id']}: {len(r['quotes'])} priced, {len(r['missed'])} missed")
    if r["missed"]:
        print("missed:", " ".join(r["missed"][:60]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
