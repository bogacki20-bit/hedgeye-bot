"""factor_board.py: what's working in the market, measured with factors we can actually OWN (Kris, 11/14/2026).

Kris: "it should be based on factors that we are actually able to own, not Goldman's baskets that we
have no idea what's in them." So every factor here is an ETF (or an ETF vs SPY / ETF vs ETF spread).
Hedgeye's style-factor baskets (tms_decks.style_factors) stay context only.

Two parts:
  1. WHAT'S WORKING: 1-day / 1-week / 1-month / 3-month returns of factor spreads and the 11 sector SPDRs.
  2. THE BOOK'S TILT: the book's 60-day beta to each factor spread, from today's holdings
     (book_positions, latest snapshot; shorts negative; cash parking excluded).

    py -m tools.factor_board
    py -m tools.factor_board --json
Read-only. No LLM calls.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

# (label, long leg, short leg). "Long leg minus short leg" = the factor's return.
FACTORS = [
    ("High beta vs low beta", "SPHB", "SPLV"),
    ("Momentum vs market", "MTUM", "SPY"),
    ("Quality vs market", "QUAL", "SPY"),
    ("Low volatility vs market", "USMV", "SPY"),
    ("Value vs growth", "IWD", "IWF"),
    ("Small caps vs large", "IWM", "SPY"),
    ("Equal weight vs cap weight", "RSP", "SPY"),
    ("Dividend payers vs market", "SCHD", "SPY"),
    ("Tech vs defensives", "XLK", "XLP"),
]
SECTORS = ["XLK", "XLC", "XLY", "XLF", "XLI", "XLB", "XLE", "XLU", "XLP", "XLV", "XLRE"]
SECTOR_NAME = {"XLK": "Tech", "XLC": "Comm services", "XLY": "Discretionary", "XLF": "Financials", "XLI": "Industrials",
               "XLB": "Materials", "XLE": "Energy", "XLU": "Utilities", "XLP": "Staples", "XLV": "Health care",
               "XLRE": "Real estate"}
CASH = {"SPAXX", "FDRXX", "BUXX", "CORE", "PENDING ACTIVITY"}
WINDOWS = {"1d": 1, "1w": 5, "1m": 21, "3m": 63}


def _book() -> dict[str, float]:
    import psycopg2
    from db_pg import _load_dotenv_fallback
    _load_dotenv_fallback()
    with psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute("SELECT symbol, sum(market_value) FROM book_positions WHERE snapshot_date=(SELECT max(snapshot_date) "
                    "FROM book_positions) AND symbol IS NOT NULL GROUP BY 1")
        rows = cur.fetchall()
    return {s.strip().upper().replace("*", ""): float(v) for s, v in rows
            if v is not None and s.strip().upper().replace("*", "") not in CASH and float(v) != 0}


def build() -> dict:
    import numpy as np
    import yfinance as yf
    book = _book()
    legs = sorted({x for _, a, b in FACTORS for x in (a, b)} | set(SECTORS) | set(book))
    px = yf.download(legs, period="8mo", interval="1d", auto_adjust=True, progress=False)["Close"]
    px = px.dropna(how="all")
    ret = px.pct_change()

    def tot(t, n):
        s = px[t].dropna()
        return (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100 if len(s) > n else None

    working = []
    for label, a, b in FACTORS:
        row = {"factor": label, "long": a, "short": b}
        for k, n in WINDOWS.items():
            ra, rb = tot(a, n), tot(b, n)
            row[k] = None if ra is None or rb is None else ra - rb
        working.append(row)
    sectors = []
    for t in SECTORS:
        row = {"etf": t, "name": SECTOR_NAME[t]}
        for k, n in WINDOWS.items():
            row[k] = tot(t, n)
        sectors.append(row)

    # The book's 60-day daily return series from today's weights (gross-normalized; shorts negative).
    held = [t for t in book if t in ret.columns and ret[t].dropna().shape[0] > 40]
    gross = sum(abs(book[t]) for t in held) or 1.0
    w = {t: book[t] / gross for t in held}
    r60 = ret.iloc[-60:]
    port = sum(r60[t].fillna(0) * w[t] for t in held)
    tilts = []
    for label, a, b in FACTORS:
        f = (r60[a] - r60[b]).fillna(0)
        var = float(np.var(f))
        beta = float(np.cov(port, f)[0, 1] / var) if var else None
        corr = float(np.corrcoef(port, f)[0, 1]) if var else None
        tilts.append({"factor": label, "beta": beta, "corr": corr})
    spy = r60["SPY"].fillna(0)
    book_beta = float(np.cov(port, spy)[0, 1] / np.var(spy))
    missing = sorted(set(book) - set(held))
    return {"as_of": str(px.index[-1].date()), "working": working, "sectors": sectors, "tilts": tilts,
            "book_beta_spy": book_beta, "names_used": len(held), "names_missing_prices": missing}


def render(d: dict) -> str:
    f = lambda x: "  n/a" if x is None else f"{x:+5.1f}"
    L = [f"WHAT'S WORKING (ownable factor ETFs, as of {d['as_of']}): spread = long leg minus short leg, %"]
    L.append(f"{'factor':28} {'long-short':12} {'1d':>6} {'1w':>6} {'1m':>6} {'3m':>6}")
    for r in sorted(d["working"], key=lambda r: -(r["1m"] or -99)):
        L.append(f"{r['factor']:28} {r['long'] + '-' + r['short']:12} {f(r['1d'])} {f(r['1w'])} {f(r['1m'])} {f(r['3m'])}")
    L.append("")
    L.append("SECTORS (%, best 1-month first)")
    for r in sorted(d["sectors"], key=lambda r: -(r["1m"] or -99)):
        L.append(f"  {r['etf']:5} {r['name']:14} 1w {f(r['1w'])}  1m {f(r['1m'])}  3m {f(r['3m'])}")
    L.append("")
    L.append(f"YOUR BOOK'S TILT (60-day beta of today's book to each factor; + = you're long the factor). "
             f"Book beta to SPY {d['book_beta_spy']:+.2f}; {d['names_used']} names used")
    for t in sorted(d["tilts"], key=lambda t: -abs(t["beta"] or 0)):
        if t["beta"] is None:
            continue
        lean = "LONG" if t["beta"] > 0.1 else ("SHORT" if t["beta"] < -0.1 else "neutral")
        L.append(f"  {t['factor']:28} beta {t['beta']:+.2f} (corr {t['corr']:+.2f})  -> {lean}")
    if d["names_missing_prices"]:
        L.append("  (no price history, left out: " + ", ".join(d["names_missing_prices"]) + ")")
    return "\n".join(L)


def hud(d: dict) -> str:
    """Rotate from -> to in 5 lines: top/bottom 3 factor spreads and sectors by 1-month return, plus the book's
    biggest factor leans (DAILY_RUNBOOK check-ins: factor_board --hud). 11/19: the 11/14 rewrite dropped hud(), so
    desk_snapshot's factor section failed with "cannot import name 'hud'" in every pack."""
    by1m = lambda xs: sorted([x for x in xs if x["1m"] is not None], key=lambda x: -x["1m"])
    f, s = by1m(d["working"]), by1m(d["sectors"])
    fj = lambda xs: " · ".join(f"{x['long']}-{x['short']} {x['1m']:+.1f}" for x in xs)
    sj = lambda xs: " · ".join(f"{x['etf']} {x['1m']:+.1f}" for x in xs)
    lean = [t for t in sorted(d["tilts"], key=lambda t: -abs(t["beta"] or 0)) if t["beta"] is not None][:3]
    return "\n".join([
        f"FACTORS LEAD (1m %): {fj(f[:3])}", f"FACTORS LAG (1m %): {fj(f[-3:][::-1])}",
        f"SECTORS IN (1m %): {sj(s[:3])}", f"SECTORS OUT (1m %): {sj(s[-3:][::-1])}",
        f"BOOK LEANS (60d beta; SPY {d['book_beta_spy']:+.2f}): "
        + " · ".join(f"{t['factor']} {t['beta']:+.2f}" for t in lean) + f"  [as of {d['as_of']}]"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hud", action="store_true", help="5-line rotate from -> to summary")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    d = build()
    print(json.dumps(d, indent=1, default=str) if a.json else hud(d) if a.hud else render(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
