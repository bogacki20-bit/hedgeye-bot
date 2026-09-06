"""
rs_corr.py – TIER 2 (SECTORS FIRST): relative strength + sector correlation.
SHADOW MODE. Prints tables only; gates nothing; touches no money path.

Reads 2yr daily closes from price_history (see tools/price_backfill.py).
Reuses the same Pearson-on-daily-returns idea already in screener.py, with
the 2.4 fixes: BOUNDED windows (20d/60d, explicit), pair ordering
(ticker_a <= ticker_b), a method stamp, n_obs kept, and None (never a garbage
number) below CORR_MIN_N.

Usage:
    python -m tools.rs_corr          # uses DATABASE_URL from env
    python tools/rs_corr.py
"""
from __future__ import annotations
import logging, math, os
import psycopg2

log = logging.getLogger("rs_corr")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SECTORS   = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
BENCHES   = ["SPY", "TLT", "SHY", "HYG"]
SPREADS   = [("HYG", "TLT"), ("LQD", "TLT")]   # credit vs duration (the sharp lines)
UNIVERSE  = sorted(set(SECTORS + BENCHES + ["LQD", "UUP"]))
WINDOWS   = [20, 60]
CORR_MIN_N = 20
METHOD    = "pearson_logret_v1"   # bump if math changes


# ── pure math ────────────────────────────────────────────────────────────────

def log_returns(closes: list[tuple]) -> dict:
    """[(date, close)] ascending -> {date: log_return}. Drops non-positive."""
    out, prev = {}, None
    for d, c in closes:
        c = float(c)
        if prev is not None and prev > 0 and c > 0:
            out[d] = math.log(c / prev)
        prev = c
    return out


def pearson(xs: list, ys: list) -> tuple:
    """Returns (corr, n). corr is None if n < CORR_MIN_N or zero variance."""
    n = len(xs)
    if n < CORR_MIN_N:
        return None, n
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx  = sum((x - mx) ** 2 for x in xs)
    vy  = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None, n
    return cov / math.sqrt(vx * vy), n


def corr_pair(ra: dict, rb: dict, window: int) -> tuple:
    """ra, rb: {date: ret}. Correlation over last `window` COMMON dates."""
    common = sorted(set(ra) & set(rb))[-window:]
    return pearson([ra[d] for d in common], [rb[d] for d in common])


def pair_key(a: str, b: str) -> tuple:
    """Enforce ticker_a <= ticker_b so corr(X,Y) and corr(Y,X) never split rows."""
    return (a, b) if a <= b else (b, a)


def avg_pairwise(returns_by: dict, tickers: list, window: int) -> tuple:
    """Mean of all defined sector-pair correlations = diversification regime.
    Rising toward 1 = everything becoming one trade (the April tell)."""
    vals = []
    for i in range(len(tickers)):
        for j in range(i + 1, len(tickers)):
            c, _ = corr_pair(
                returns_by.get(tickers[i], {}),
                returns_by.get(tickers[j], {}),
                window,
            )
            if c is not None:
                vals.append(c)
    avg = (sum(vals) / len(vals)) if vals else None
    return avg, len(vals)


def rs_trend(a_closes: list, b_closes: list, window: int) -> tuple:
    """Relative strength = ratio a/b.
    Returns (level, pct_change_over_window, direction).
    Rising ratio = a beating b. Aligns on common dates."""
    da = {d: float(c) for d, c in a_closes}
    db = {d: float(c) for d, c in b_closes}
    common = sorted(set(da) & set(db))
    if len(common) < 2:
        return None, None, "n/a"
    ratio = [(d, da[d] / db[d]) for d in common if db[d] > 0]
    if len(ratio) < 2:
        return None, None, "n/a"
    seg   = ratio[-(window + 1):] if len(ratio) > window else ratio
    lvl   = seg[-1][1]
    base  = seg[0][1]
    chg   = (lvl / base - 1.0) if base > 0 else None
    if chg is None:
        direction = "n/a"
    elif chg > 0.002:
        direction = "rising"
    elif chg < -0.002:
        direction = "falling"
    else:
        direction = "flat"
    return lvl, chg, direction


# ── DB I/O ───────────────────────────────────────────────────────────────────

def load_closes(cur, tickers: list, lookback_days: int = 400) -> dict:
    """Returns {ticker: [(date, close), ...]} ascending."""
    cur.execute(
        "SELECT ticker, d, close FROM price_history "
        "WHERE ticker = ANY(%s) AND d >= CURRENT_DATE - %s "
        "ORDER BY ticker, d",
        (list(tickers), lookback_days),
    )
    out: dict = {}
    for t, d, c in cur.fetchall():
        out.setdefault(t, []).append((d, c))
    return out


# ── display ──────────────────────────────────────────────────────────────────

def print_rs(closes_by: dict, window: int) -> None:
    print(f"\nRELATIVE STRENGTH  (ratio {window}d trend; rising = name beats the hurdle)")

    # spreads
    for a, b in SPREADS:
        if a in closes_by and b in closes_by:
            lvl, chg, dir_ = rs_trend(closes_by[a], closes_by[b], window)
            chg_s = f"{chg:+.1%}" if chg is not None else " n/a "
            print(f"  {a}/{b} = {lvl:.3f}  {chg_s}  {dir_}")

    # sectors vs each bench
    header = f"  {'sector':<6}" + "".join(f"  vs{b:<5}" for b in BENCHES)
    print(header)
    for sec in SECTORS:
        if sec not in closes_by:
            continue
        cells = []
        for b in BENCHES:
            if b not in closes_by:
                cells.append("   n/a ")
                continue
            _, chg, _ = rs_trend(closes_by[sec], closes_by[b], window)
            cells.append(f"{chg:+6.1%}" if chg is not None else "   n/a ")
        print(f"  {sec:<6}" + "  ".join(cells))


def print_corr(returns_by: dict, window: int) -> None:
    print(f"\nSECTOR CORRELATION  —  diversification regime  (window={window}d)")
    avg, n_pairs = avg_pairwise(returns_by, SECTORS, window)
    if avg is None:
        print("  insufficient data")
        return
    bar = "█" * int(max(0.0, (avg + 1) / 2) * 20)
    print(f"  avg pairwise = {avg:+.2f}  ({n_pairs} defined pairs)  [{bar:<20}]")
    print(f"  -> 0 = decorrelated  -> +1 = one trade (April danger zone)")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        closes_by = load_closes(cur, UNIVERSE)
    finally:
        conn.close()

    if not closes_by:
        print("No price data found. Run tools/price_backfill.py first.")
        return

    returns_by = {t: log_returns(c) for t, c in closes_by.items()}

    print("=" * 70)
    print("TIER 2  —  RELATIVE STRENGTH + CORRELATION  (shadow mode)")
    print("=" * 70)

    for w in WINDOWS:
        print_rs(closes_by, w)
        print_corr(returns_by, w)

    print()


if __name__ == "__main__":
    main()
