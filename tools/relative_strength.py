"""Relative strength + sector-correlation matrix ("the puck" table).

Roadmap tier 1. For the sector-ETF universe (11 SPDR sectors) vs a
benchmark (default SPY) this computes, per name:

  * rs_trade / rs_trend / rs_tail — percent change of the sector/benchmark
    RS ratio over ~10 / ~60 / ~200 sessions (Hedgeye TRADE/TREND/TAIL).
  * rs_slope — normalized per-day OLS slope of the RS ratio over the short
    window. THE TELL: a name can rank #1 on rs_trend and still be rolling
    over (rs_slope < 0).
  * rank_trade / rank_trend / rank_tail — 1..N, 1 = strongest.
  * rp — Hedgeye range position, joined from hedgeye_risk_ranges:
    rp = (price - buy_trade) / (sell_trade - buy_trade). A negative/over-1
    rp is FLAGGED (range_broken), never voided here.
  * grid_cell — the 4-cell read:
        RS high + rp low  -> PASS_THE_PUCK (leader on sale)
        RS high + rp high -> HOLD (don't add)
        RS low  + rp low  -> TRAP (falling knife)
        RS low  + rp high -> FADE / short

It also builds the sector-vs-sector daily-return correlation matrix over
CORR_WINDOW, persists each pair to correlation_snapshots, and records the
average pairwise correlation (crowding / diversification-regime gauge) to
diversification_snapshots.

rp says WHERE a name sits; RS says whether it is a leader or a knife;
avg pairwise correlation says whether the book is really diversified.

Price source is vendor-agnostic (see fetch_close_series). Default is
yfinance; a paid daily-OHLCV vendor (Polygon / Tiingo / EODHD) drops in at
the marked hook without touching any of the math.

CLI:
    python -m tools.relative_strength            # compute + persist
    python -m tools.relative_strength --dry-run  # compute + print only
    python -m tools.relative_strength --benchmark SPY --corr-window 60
"""

from __future__ import annotations

import os
import sys
import logging
import datetime as dt
from typing import Optional

log = logging.getLogger(__name__)

# ── Universe & parameters ───────────────────────────────────────────────────
# 11 SPDR sector ETFs — matches the "rank 1..11" grid in the spec.
SECTOR_ETFS = ["XLK", "XLV", "XLF", "XLE", "XLI",
               "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
BENCHMARK = "SPY"

# Bond / credit / dollar ETFs added to the RS universe so they rank against
# SPY alongside the sectors (TLT strong vs SPY = risk-off; HYG weak = credit
# stress). Roadmap benchmarks: SPY, TLT, SHY, HYG, LQD (+ UUP for correlation).
MACRO_ETFS = ["TLT", "SHY", "HYG", "LQD", "UUP"]
UNIVERSE = SECTOR_ETFS + MACRO_ETFS

# Per-ticker return correlation is stored against each of these (roadmap: SPY
# and UUP). SPY = beta-ish; UUP = dollar sensitivity.
CORR_BENCHMARKS = ["SPY", "UUP"]

# Risk-on/off + credit-quality ratios (spec #5). RS(HYG vs TLT) == HYG/TLT
# momentum, so these are just RS rows with ticker=HYG, benchmark=TLT / LQD.
# Rising = risk-on / credit leading.
RORO_PAIRS = [("HYG", "TLT"), ("HYG", "LQD")]

TRADE_WINDOW = 10     # sessions — Hedgeye TRADE horizon for RS
TREND_WINDOW = 60     # sessions — Hedgeye TREND
TAIL_WINDOW = 200     # sessions — Hedgeye TAIL
SLOPE_WINDOW = 10     # sessions used for the rolling-over slope
CORR_WINDOW = 60      # sessions for the sector-vs-sector correlation matrix

# Diversification-regime thresholds on the average pairwise correlation.
_DIV_TIGHT = 0.70     # crowded — fake-diversification risk (the energy lesson)
_DIV_LOOSE = 0.40     # real dispersion

# ── Price vendor seam ────────────────────────────────────────────────────────
def fetch_close_series(ticker: str, lookback_days: int) -> list[tuple[str, float]]:
    """Return [(iso_date, close)] oldest->newest for the last `lookback_days`
    trading sessions, via the configured vendor.

    Default vendor is yfinance. To move to a paid daily-OHLCV feed, implement
    a `_<vendor>_close_series(ticker, lookback_days)` with the SAME return
    shape and register it below (or gate on RS_PRICE_VENDOR). None of the RS
    or correlation math changes — it only consumes (date, close) pairs.
    """
    vendor = os.environ.get("RS_PRICE_VENDOR", "yfinance").lower()
    if vendor == "yfinance":
        return _yf_close_series(ticker, lookback_days)
    # ── paid-vendor hook ─────────────────────────────────────────────────────
    # elif vendor == "polygon":
    #     return _polygon_close_series(ticker, lookback_days)
    # elif vendor == "tiingo":
    #     return _tiingo_close_series(ticker, lookback_days)
    raise NotImplementedError(
        f"RS_PRICE_VENDOR={vendor!r} not wired; only 'yfinance' is available. "
        "Add a _<vendor>_close_series() returning [(iso_date, close)] and "
        "register it in fetch_close_series()."
    )


def _yf_close_series(ticker: str, lookback_days: int) -> list[tuple[str, float]]:
    try:
        import yfinance as yf
    except Exception as e:  # pragma: no cover - env dependent
        log.warning("yfinance unavailable: %s", e)
        return []
    try:
        # Pad for weekends/holidays; ~1.5x calendar days covers trading days.
        period = f"{int(lookback_days * 1.6) + 15}d"
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df is None or df.empty or "Close" not in df:
            return []
        out: list[tuple[str, float]] = []
        for idx, val in df["Close"].items():
            if val != val:  # NaN
                continue
            out.append((idx.date().isoformat(), float(val)))
        return out[-lookback_days:] if lookback_days else out
    except Exception as e:  # pragma: no cover - network dependent
        log.warning("yfinance history failed for %s: %s", ticker, e)
        return []


# ── Pure math (no I/O — unit-testable) ───────────────────────────────────────
def align_on_date(a: list[tuple[str, float]],
                  b: list[tuple[str, float]]) -> tuple[list[str], list[float], list[float]]:
    """Inner-join two [(date, close)] series on date. Returns aligned
    (dates, a_closes, b_closes) sorted oldest->newest."""
    bm = dict(b)
    dates, av, bv = [], [], []
    for d, av_ in a:
        if d in bm:
            dates.append(d)
            av.append(av_)
            bv.append(bm[d])
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    return ([dates[i] for i in order],
            [av[i] for i in order],
            [bv[i] for i in order])


def rs_ratio_series(sector_closes: list[float],
                    bench_closes: list[float]) -> list[float]:
    """Relative-strength ratio = sector / benchmark, elementwise."""
    return [s / b for s, b in zip(sector_closes, bench_closes) if b]


def roc(series: list[float], window: int) -> Optional[float]:
    """Percent change of series over `window` steps (rate of change)."""
    if len(series) <= window:
        return None
    past = series[-1 - window]
    if not past:
        return None
    return series[-1] / past - 1.0


def norm_slope(series: list[float], window: int) -> Optional[float]:
    """Normalized per-step OLS slope of the tail of `series`.

    Slope of a least-squares line fit to the last `window` points, divided by
    the window mean so it reads as a per-day fractional drift. Sign answers
    "is this rolling over?" independent of the level rank.
    """
    if len(series) < max(window, 3):
        return None
    ys = series[-window:]
    n = len(ys)
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0 or my == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return slope / my


def returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        p0 = closes[i - 1]
        if p0:
            out.append(closes[i] / p0 - 1.0)
    return out


def pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < 5:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def rank_desc(values: dict[str, Optional[float]]) -> dict[str, Optional[int]]:
    """1 = highest value. Names with None are unranked (None)."""
    present = [(t, v) for t, v in values.items() if v is not None]
    present.sort(key=lambda kv: kv[1], reverse=True)
    out: dict[str, Optional[int]] = {t: None for t in values}
    for i, (t, _) in enumerate(present):
        out[t] = i + 1
    return out


def grid_cell(rs_high: bool, rp: Optional[float]) -> Optional[str]:
    """4-cell classification. Needs rp; returns None without it."""
    if rp is None:
        return None
    rp_low = rp < 0.5
    if rs_high and rp_low:
        return "PASS_THE_PUCK"
    if rs_high and not rp_low:
        return "HOLD"
    if not rs_high and rp_low:
        return "TRAP"
    return "FADE"


def diversification_regime(avg_corr: Optional[float]) -> Optional[str]:
    if avg_corr is None:
        return None
    if avg_corr >= _DIV_TIGHT:
        return "tight"
    if avg_corr < _DIV_LOOSE:
        return "loose"
    return "normal"


# ── Orchestration ────────────────────────────────────────────────────────────
def _compute(benchmark: str, corr_window: int) -> dict:
    """Fetch prices once and compute the full RS + correlation payload for the
    UNIVERSE (11 sectors + TLT/SHY/HYG/LQD/UUP) vs `benchmark`, plus per-ticker
    correlation to SPY & UUP and the HYG/TLT + HYG/LQD RORO ratios. Pure of DB
    writes so it can be printed in --dry-run."""
    lookback = max(TAIL_WINDOW + SLOPE_WINDOW, corr_window) + 25
    need = list(dict.fromkeys(UNIVERSE + [benchmark] + CORR_BENCHMARKS))
    prices: dict[str, list[tuple[str, float]]] = {}
    for t in need:
        prices[t] = fetch_close_series(t, lookback)

    bench = prices.get(benchmark) or []
    rows: dict[str, dict] = {}

    for t in UNIVERSE:
        if t == benchmark:
            continue
        sec = prices.get(t) or []
        if not sec or not bench:
            log.warning("no price data for %s or benchmark; skipping", t)
            continue
        _dates, sc, bc = align_on_date(sec, bench)
        ratio = rs_ratio_series(sc, bc)
        if len(ratio) < TRADE_WINDOW + 1:
            log.warning("insufficient aligned history for %s (n=%d)", t, len(ratio))
            continue
        rows[t] = {
            "ticker": t,
            "last_close": sec[-1][1],
            "rs_trade": roc(ratio, TRADE_WINDOW),
            "rs_trend": roc(ratio, TREND_WINDOW),
            "rs_tail": roc(ratio, TAIL_WINDOW),
            "rs_slope": norm_slope(ratio, SLOPE_WINDOW),
            "n_obs": len(ratio),
        }

    # Ranks per duration — across the whole universe vs this benchmark.
    rt = rank_desc({t: r["rs_trade"] for t, r in rows.items()})
    rr = rank_desc({t: r["rs_trend"] for t, r in rows.items()})
    rl = rank_desc({t: r["rs_tail"] for t, r in rows.items()})
    universe_n = len(rows)
    rs_high_cut = (universe_n + 1) // 2  # top half by TREND rank = "leader"

    for t, r in rows.items():
        r["rank_trade"] = rt[t]
        r["rank_trend"] = rr[t]
        r["rank_tail"] = rl[t]
        r["universe_n"] = universe_n
        rs_high = r["rank_trend"] is not None and r["rank_trend"] <= rs_high_cut
        r["rs_high"] = rs_high
        r["rolling_over"] = bool(rs_high and (r["rs_slope"] or 0) < 0)

    # ── rp join from mfr ranges ─────────────────────────────────────────────
    ranges = _load_active_ranges()
    for t, r in rows.items():
        rp = None
        broken = None
        rng = ranges.get(t)
        if rng:
            lo, hi = rng
            if hi is not None and lo is not None and (hi - lo) != 0:
                rp = (r["last_close"] - lo) / (hi - lo)
                broken = rp < 0 or rp > 1
        r["rp"] = rp
        r["range_broken"] = broken
        r["grid_cell"] = grid_cell(r.get("rs_high", False), rp)

    # ── Correlation matrix over the FULL universe (captures the HYG-TLT credit
    #    relationship, stock/bond correlation, etc.) ──
    pairs: list[dict] = []
    uni = [t for t in UNIVERSE if prices.get(t)]
    for i in range(len(uni)):
        for j in range(i + 1, len(uni)):
            a, b = uni[i], uni[j]
            _d, ca, cb = align_on_date(prices[a], prices[b])
            ra, rb = returns(ca), returns(cb)
            c = pearson(ra[-corr_window:], rb[-corr_window:])
            if c is None:
                continue
            pairs.append({"a": a, "b": b, "corr": round(c, 5),
                          "n": min(len(ra[-corr_window:]), len(rb[-corr_window:]))})

    # Diversification gauge stays SECTOR-only (its job is equity-book crowding;
    # including bonds would dilute the signal).
    sec_corrs = [p["corr"] for p in pairs
                 if p["a"] in SECTOR_ETFS and p["b"] in SECTOR_ETFS]
    avg_corr = round(sum(sec_corrs) / len(sec_corrs), 5) if sec_corrs else None
    div = {
        "window": corr_window,
        "avg_pairwise_corr": avg_corr,
        "max_pairwise_corr": round(max(sec_corrs), 5) if sec_corrs else None,
        "min_pairwise_corr": round(min(sec_corrs), 5) if sec_corrs else None,
        "n_pairs": len(sec_corrs),
        "regime": diversification_regime(avg_corr),
    }

    # ── Per-ticker correlation to SPY and UUP (roadmap) ──
    corr_to_bench: list[dict] = []
    for tgt in CORR_BENCHMARKS:
        tp = prices.get(tgt) or []
        if not tp:
            continue
        for t in UNIVERSE:
            if t == tgt:
                continue
            sp = prices.get(t) or []
            if not sp:
                continue
            _d, ca, cb = align_on_date(sp, tp)
            c = pearson(returns(ca)[-corr_window:], returns(cb)[-corr_window:])
            if c is None:
                continue
            corr_to_bench.append({"a": t, "b": tgt, "corr": round(c, 5),
                                  "n": min(corr_window, max(len(ca) - 1, 0))})

    # ── RORO / credit-quality ratios (spec #5): RS of HYG vs TLT and vs LQD.
    #    Rising = risk-on / credit leading. ──
    roro: list[dict] = []
    for num, den in RORO_PAIRS:
        np_, dp = prices.get(num) or [], prices.get(den) or []
        if not np_ or not dp:
            continue
        _d, nc, dc = align_on_date(np_, dp)
        ratio = rs_ratio_series(nc, dc)
        if len(ratio) < TRADE_WINDOW + 1:
            continue
        roro.append({
            "ticker": num, "benchmark": den,
            "rs_trade": roc(ratio, TRADE_WINDOW),
            "rs_trend": roc(ratio, TREND_WINDOW),
            "rs_tail": roc(ratio, TAIL_WINDOW),
            "rs_slope": norm_slope(ratio, SLOPE_WINDOW),
            "n_obs": len(ratio),
        })

    return {"rows": rows, "pairs": pairs, "diversification": div,
            "benchmark": benchmark, "corr_to_bench": corr_to_bench,
            "roro": roro}


def _load_active_ranges() -> dict[str, tuple[float, float]]:
    """Map ticker -> (range_low, range_high) from mfr_snapshots — the same
    stored range source REPORT / REPORT NOW use for rp on master. Latest row
    per ticker with a defined range. Returns {} if the DB is unreachable
    (RS still computes, rp just NULL)."""
    try:
        import db_pg
        out: dict[str, tuple[float, float]] = {}
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (ticker) ticker, range_low, range_high
                FROM mfr_snapshots
                WHERE range_low IS NOT NULL AND range_high IS NOT NULL
                  AND range_high > range_low
                ORDER BY ticker, snapshot_date DESC
                """
            )
            for ticker, lo, hi in cur.fetchall():
                out[ticker] = (float(lo), float(hi))
        return out
    except Exception as e:
        log.warning("mfr range join unavailable (rp will be NULL): %s", e)
        return {}


def _print(payload: dict) -> None:
    rows = sorted(payload["rows"].values(),
                  key=lambda r: (r["rank_trend"] is None, r["rank_trend"] or 999))
    bm = payload["benchmark"]
    print(f"\nRELATIVE STRENGTH vs {bm}  (1 = strongest)\n")
    hdr = (f"{'TKR':<5} {'TRADE':>8} {'TREND':>8} {'TAIL':>8} "
           f"{'SLOPE':>9} {'rk_td':>5} {'rk_tr':>5} {'rk_tl':>5} "
           f"{'rp':>6} {'CELL':<14}")
    print(hdr)
    print("-" * len(hdr))
    def pct(x):
        return f"{x*100:+.2f}%" if x is not None else "n/a"

    for r in rows:
        rp = r.get("rp")
        rp_s = f"{rp:+.2f}" if rp is not None else "n/a"
        slope = r.get("rs_slope")
        slope_s = f"{slope:+.5f}" if slope is not None else "n/a"
        rk_td = str(r.get("rank_trade") or "-")
        rk_tr = str(r.get("rank_trend") or "-")
        rk_tl = str(r.get("rank_tail") or "-")
        cell = r.get("grid_cell") or "-"
        flag = ""
        if r.get("range_broken"):
            flag += "!"
        if r.get("rolling_over"):
            flag += "v"
        print(f"{r['ticker']:<5} {pct(r['rs_trade']):>8} {pct(r['rs_trend']):>8} "
              f"{pct(r['rs_tail']):>8} {slope_s:>9} "
              f"{rk_td:>5} {rk_tr:>5} {rk_tl:>5} {rp_s:>6} {cell:<14}{flag}")

    d = payload["diversification"]
    print(f"\nDIVERSIFICATION GAUGE ({d['window']}d, {d['n_pairs']} pairs): "
          f"avg pairwise corr = "
          f"{d['avg_pairwise_corr'] if d['avg_pairwise_corr'] is not None else 'n/a'} "
          f"[{d['regime'] or 'n/a'}]  "
          f"(min {d['min_pairwise_corr']}, max {d['max_pairwise_corr']})")
    roro = payload.get("roro", [])
    if roro:
        def _rd(num, den):
            m = next((x for x in roro
                      if x["ticker"] == num and x["benchmark"] == den), None)
            v = m["rs_trend"] if m else None
            return f"{v*100:+.1f}%" if v is not None else "n/a"
        print(f"\nRORO (60d ratio momentum, + = risk-on / credit leading): "
              f"HYG/TLT {_rd('HYG','TLT')} · credit HYG/LQD {_rd('HYG','LQD')}")

    pucks = [r["ticker"] for r in rows if r.get("grid_cell") == "PASS_THE_PUCK"]
    if pucks:
        print(f"\n🟢 PASS THE PUCK (leader on sale): {', '.join(pucks)}")
    print()


def _persist(payload: dict, snapshot_date: dt.date) -> int:
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr)
        return 2
    # ON CONFLICT upserts make this idempotent — a retried persist can't dupe.
    def _do():
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for r in payload["rows"].values():
                try:
                    cur.execute(
                        """
                        INSERT INTO rs_snapshots
                          (snapshot_date, ticker, benchmark, rs_trade, rs_trend,
                           rs_tail, rs_slope, rank_trade, rank_trend, rank_tail,
                           universe_n, rp, range_broken, grid_cell, rolling_over,
                           n_obs)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (snapshot_date, ticker, benchmark) DO UPDATE SET
                           rs_trade=EXCLUDED.rs_trade, rs_trend=EXCLUDED.rs_trend,
                           rs_tail=EXCLUDED.rs_tail, rs_slope=EXCLUDED.rs_slope,
                           rank_trade=EXCLUDED.rank_trade, rank_trend=EXCLUDED.rank_trend,
                           rank_tail=EXCLUDED.rank_tail, universe_n=EXCLUDED.universe_n,
                           rp=EXCLUDED.rp, range_broken=EXCLUDED.range_broken,
                           grid_cell=EXCLUDED.grid_cell, rolling_over=EXCLUDED.rolling_over,
                           n_obs=EXCLUDED.n_obs, computed_at=NOW()
                        """,
                        (snapshot_date, r["ticker"], payload["benchmark"],
                         r["rs_trade"], r["rs_trend"], r["rs_tail"], r["rs_slope"],
                         r.get("rank_trade"), r.get("rank_trend"), r.get("rank_tail"),
                         r.get("universe_n"), r.get("rp"), r.get("range_broken"),
                         r.get("grid_cell"), r.get("rolling_over"), r.get("n_obs")),
                    )
                except Exception as e:
                    log.warning("rs_snapshots insert failed for %s: %s", r["ticker"], e)

            for p in payload["pairs"]:
                try:
                    cur.execute(
                        """
                        INSERT INTO correlation_snapshots
                          (snapshot_date, ticker_a, ticker_b, window_days,
                           correlation, n_obs)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (snapshot_date, ticker_a, ticker_b, window_days)
                          DO UPDATE SET correlation=EXCLUDED.correlation,
                                        n_obs=EXCLUDED.n_obs, computed_at=NOW()
                        """,
                        (snapshot_date, p["a"], p["b"],
                         payload["diversification"]["window"], p["corr"], p["n"]),
                    )
                except Exception as e:
                    log.warning("correlation insert failed %s-%s: %s", p["a"], p["b"], e)

            # per-ticker correlation to SPY / UUP → correlation_snapshots
            for p in payload.get("corr_to_bench", []):
                try:
                    cur.execute(
                        """
                        INSERT INTO correlation_snapshots
                          (snapshot_date, ticker_a, ticker_b, window_days,
                           correlation, n_obs)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (snapshot_date, ticker_a, ticker_b, window_days)
                          DO UPDATE SET correlation=EXCLUDED.correlation,
                                        n_obs=EXCLUDED.n_obs, computed_at=NOW()
                        """,
                        (snapshot_date, p["a"], p["b"],
                         payload["diversification"]["window"], p["corr"], p["n"]),
                    )
                except Exception as e:
                    log.warning("corr-to-bench insert failed %s-%s: %s",
                                p["a"], p["b"], e)

            # RORO / credit ratios → rs_snapshots (ticker=HYG, benchmark=TLT/LQD)
            for r in payload.get("roro", []):
                try:
                    cur.execute(
                        """
                        INSERT INTO rs_snapshots
                          (snapshot_date, ticker, benchmark, rs_trade, rs_trend,
                           rs_tail, rs_slope, n_obs)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (snapshot_date, ticker, benchmark) DO UPDATE SET
                           rs_trade=EXCLUDED.rs_trade, rs_trend=EXCLUDED.rs_trend,
                           rs_tail=EXCLUDED.rs_tail, rs_slope=EXCLUDED.rs_slope,
                           n_obs=EXCLUDED.n_obs, computed_at=NOW()
                        """,
                        (snapshot_date, r["ticker"], r["benchmark"], r["rs_trade"],
                         r["rs_trend"], r["rs_tail"], r["rs_slope"], r["n_obs"]),
                    )
                except Exception as e:
                    log.warning("roro insert failed %s/%s: %s",
                                r["ticker"], r["benchmark"], e)

            d = payload["diversification"]
            try:
                cur.execute(
                    """
                    INSERT INTO diversification_snapshots
                      (snapshot_date, window_days, universe, avg_pairwise_corr,
                       max_pairwise_corr, min_pairwise_corr, n_pairs, regime)
                    VALUES (%s,%s,'sector_spdr',%s,%s,%s,%s,%s)
                    ON CONFLICT (snapshot_date, window_days, universe) DO UPDATE SET
                       avg_pairwise_corr=EXCLUDED.avg_pairwise_corr,
                       max_pairwise_corr=EXCLUDED.max_pairwise_corr,
                       min_pairwise_corr=EXCLUDED.min_pairwise_corr,
                       n_pairs=EXCLUDED.n_pairs, regime=EXCLUDED.regime,
                       computed_at=NOW()
                    """,
                    (snapshot_date, d["window"], d["avg_pairwise_corr"],
                     d["max_pairwise_corr"], d["min_pairwise_corr"],
                     d["n_pairs"], d["regime"]),
                )
            except Exception as e:
                log.warning("diversification insert failed: %s", e)
            conn.commit()

    try:
        db_pg.with_db_retry(_do)
        return 0
    except Exception as e:
        print(f"ERROR: persistence failed: {e}", file=sys.stderr)
        return 3


def render_report_block(benchmark: str = BENCHMARK,
                        corr_window: int = CORR_WINDOW,
                        max_names: int = 4) -> str:
    """Compact, Telegram-friendly RS block for REPORT / REPORT NOW / DAYPACK.

    READ-ONLY: reads the latest persisted rs_snapshots + diversification_snapshots
    rows (the daily `tools.relative_strength` run writes them). Does NOT fetch
    prices — keeps the report path fast and matches the "database is the desk"
    principle (bot holds state; the report renders rows). Returns a short
    'RS/GRID: ...' + 'DIVERSIFICATION: ...' string, or a one-line notice if
    there is no snapshot for today yet. Never raises.
    """
    try:
        import db_pg
        from datetime import date as _date
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker, rs_trend, rank_trend, rp, grid_cell, rolling_over,
                       range_broken, snapshot_date
                FROM rs_snapshots
                WHERE benchmark = %s
                  AND snapshot_date = (SELECT max(snapshot_date) FROM rs_snapshots
                                       WHERE benchmark = %s)
                ORDER BY rank_trend NULLS LAST
                """,
                (benchmark, benchmark),
            )
            rows = cur.fetchall()
            if not rows:
                return "RS/GRID: no snapshot (run tools.relative_strength)"
            snap = rows[0][7]
            # RS is a daily EOD signal, so through the trading day the latest
            # snapshot is the prior close — that is NOT stale. Only warn when
            # the daily job has genuinely stopped (older than a long weekend).
            age = (_date.today() - snap).days
            if age <= 0:
                stale = ""
            elif age <= 3:
                stale = f" [{snap}]"          # last close — normal pre-EOD
            else:
                stale = f" [STALE {snap}]"    # daily refresh likely stopped

            pucks, traps, leaders, laggards = [], [], [], []
            for (tkr, _rst, rk, rp, cell, rolling, broken, _d) in rows:
                tag = ""
                if broken:
                    tag += "!"
                if rolling:
                    tag += "↓"  # rolling over
                rp_s = f"{float(rp):.2f}" if rp is not None else "n/a"
                label = f"{tkr}(rk{rk or '-'},rp{rp_s}{tag})"
                if cell == "PASS_THE_PUCK":
                    pucks.append(label)
                elif cell == "TRAP":
                    traps.append(label)
                if rk is not None and rk <= 3:
                    leaders.append(tkr)
                if rk is not None and rk >= len(rows) - 2:
                    laggards.append(tkr)

            cur.execute(
                """
                SELECT avg_pairwise_corr, regime, n_pairs, window_days
                FROM diversification_snapshots
                WHERE universe = 'sector_spdr'
                ORDER BY snapshot_date DESC, window_days LIMIT 1
                """
            )
            d = cur.fetchone()

            # RORO / credit ratios: RS(HYG vs TLT) and RS(HYG vs LQD) rows.
            cur.execute(
                """
                SELECT benchmark, rs_trend FROM rs_snapshots
                WHERE ticker = 'HYG' AND benchmark IN ('TLT','LQD')
                  AND snapshot_date = (SELECT max(snapshot_date) FROM rs_snapshots
                                       WHERE ticker = 'HYG' AND benchmark = 'TLT')
                """
            )
            roro = {b: v for b, v in cur.fetchall()}

        line1 = (f"RS/GRID vs {benchmark}{stale} (rank 1-{len(rows)}): "
                 f"\U0001F7E2PUCK {' '.join(pucks[:max_names]) or 'none'} · "
                 f"\U0001F534TRAP {' '.join(traps[:max_names]) or 'none'} · "
                 f"lead {' '.join(leaders[:3]) or '-'} · "
                 f"lag {' '.join(laggards[:3]) or '-'}")
        if d and d[0] is not None:
            line2 = (f"DIVERSIFICATION {int(d[3])}d: avg corr {float(d[0]):.2f} "
                     f"[{d[1]}] ({d[2]} pairs)")
        else:
            line2 = "DIVERSIFICATION: no snapshot"

        def _r(b):
            v = roro.get(b)
            return f"{float(v)*100:+.1f}%" if v is not None else "n/a"
        if roro:
            line3 = (f"RORO: HYG/TLT {_r('TLT')} · credit HYG/LQD {_r('LQD')} "
                     f"(+ = risk-on)")
            return line1 + "\n" + line2 + "\n" + line3
        return line1 + "\n" + line2
    except Exception as e:
        log.warning("render_report_block failed: %s", e)
        return f"RS/GRID: unavailable ({e})"


def run(dry_run: bool = False, benchmark: str = BENCHMARK,
        corr_window: int = CORR_WINDOW) -> int:
    today = dt.date.today()
    payload = _compute(benchmark, corr_window)
    if not payload["rows"]:
        print("ERROR: no RS rows computed (price feed returned nothing).",
              file=sys.stderr)
        return 4
    _print(payload)
    if dry_run:
        print("[dry-run] no persistence.")
        return 0
    return _persist(payload, today)


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.relative_strength")
    p.add_argument("--dry-run", action="store_true",
                   help="compute + print only; no persistence")
    p.add_argument("--benchmark", default=BENCHMARK)
    p.add_argument("--corr-window", type=int, default=CORR_WINDOW)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run, benchmark=a.benchmark, corr_window=a.corr_window)


if __name__ == "__main__":
    raise SystemExit(_cli())
