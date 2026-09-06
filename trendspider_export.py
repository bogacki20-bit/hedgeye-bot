r"""TrendSpider custom-symbol export job — TRENDSPIDER_ML_ROUND1_SPEC_v1 Step 1.

Exports the bot's stored MFR / volume / RS / diversification / quad features to
TrendSpider custom symbols (one symbol per feature) for the Round-1 ML
feasibility test on SPY 60-min.

Extended 2026-09-06 with the TradingView MFR indicator history (2018→,
tv_mfr_history / tv_features_history, migration 085): per-asset range, LT
range, trend tag + levels, range positions, shadow Hurst, and the Macro Show
USD correlation set for SPY/UUP/VIX/USO/AAAU. Symbols with a live corpus
source splice history-before / live-after (see union_hist_live).

Doctrine (do not violate):
  * Python owns all arithmetic; no LLM calls.
  * Read ONLY stored corpus tables — never recompute a level at export time.
  * known_at = the timestamp the stored row was WRITTEN by its producing job
    (mfr_snapshots.fetched_at / volume+rs+diversification computed_at /
    quad_regime_history.effective_at) — never the market date. Source upserts
    bump their write-timestamps, so known_at can be later than first-known;
    that is the safe direction (operator decision 5, 2026-09-04) and rows are
    deduped on (symbol, source_key) so a bumped timestamp never re-exports.
  * Corpus-first: rows land in ts_export_log before/while uploading.
  * SpotGamma features are EXCLUDED (Step-0 finding: sg tables dead since
    2026-08-06, backfill-collapsed fetched_at). MFR's own zero_gamma /
    call_wall_mfr / put_wall_mfr substitute (operator decision 1).

CLI:
  py trendspider_export.py --dry-run --backfill   # CSVs to ./ts_export/, no DB writes, no upload
  py trendspider_export.py --backfill             # stage + upload full history
  py trendspider_export.py                        # incremental (default)

Validation is loud: NaN/None-shaped values, non-positive levels, timestamps
not strictly in the past, duplicate NY-minute stamps, or a known_at that is
non-monotonic vs. source order all ABORT the run (exit 1) with the offending
rows printed. Rows that are legitimately absent in the corpus (NULL column,
trendError) are skipped with a printed per-row reason — never silently.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import time
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

import db_pg  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

# No trailing slash — the slash variant 504s (TrendSpider working example,
# 2026-09-04). One symbol per POST, named in the body's "symbol" field.
DEFAULT_UPLOAD_URL = "https://charts.trendspider.com/userapi/1/data/custom_symbols"
SLEEP_BETWEEN_UPLOADS_S = 20      # rate limit: "a few calls per minute"
MAX_CSV_BYTES = 7 * 1024 * 1024   # TrendSpider upload cap
DRY_RUN_DIR = REPO_ROOT / "ts_export"

TREND_MAP = {"trendBullish": 1.0, "trendNeutral": 0.0, "trendBearish": -1.0}
DECEL_CAP = 7


# ─────────────────────────── row / result shapes ───────────────────────────

@dataclass
class Row:
    source_key: str          # dedupe identity (ISO date, or effective_at text)
    known_at: datetime       # tz-aware write timestamp of the source row
    value: float


@dataclass
class Extract:
    rows: list[Row] = field(default_factory=list)
    skips: list[tuple[str, str]] = field(default_factory=list)  # (source_key, reason)


# ─────────────────────────── extractors ───────────────────────────
#
# Each extractor takes (cur, since) where `since` is the max already-uploaded
# source_key for that symbol (or None for full history / first run) and
# returns an Extract. All SQL is SELECT-only against stored corpus tables.

def _num(raw):
    """Decimal/str/float -> float. Returns None for None."""
    return None if raw is None else float(raw)


def mfr_column(ticker: str, value_sql: str, convert=None):
    """Extractor over mfr_snapshots: one value column (or SQL expression),
    known_at = fetched_at, source_key = snapshot_date. NULL values are loud
    skips (pre-migration gaps, tickers MFR doesn't price options on, ...)."""
    def extract(cur, since) -> Extract:
        cur.execute(
            f"""
            SELECT snapshot_date::text, fetched_at, {value_sql}
              FROM mfr_snapshots
             WHERE ticker = %s AND (%s::text IS NULL OR snapshot_date::text > %s)
             ORDER BY snapshot_date
            """,
            (ticker, since, since),
        )
        out = Extract()
        for key, fetched_at, raw in cur.fetchall():
            if raw is None:
                out.skips.append((key, "NULL in source"))
                continue
            if convert is not None:
                val, reason = convert(raw)
                if reason is not None:
                    out.skips.append((key, reason))
                    continue
            else:
                val = _num(raw)
            out.rows.append(Row(key, fetched_at, val))
        return out
    return extract


def _convert_trend(raw):
    """trendBullish/Neutral/Bearish -> +1/0/-1; trendError -> loud skip
    (operator decision 6); anything else is a hard failure."""
    if raw == "trendError":
        return None, "trendError"
    if raw in TREND_MAP:
        return TREND_MAP[raw], None
    raise ValueError(f"unknown trend_signal value: {raw!r}")


def extract_decel(cur, since) -> Extract:
    """volume_snapshots SPY: -1 when distribution (price down 3d AND volume
    NOT decelerating), else decel_streak capped at DECEL_CAP (decision 4)."""
    cur.execute(
        """
        SELECT snapshot_date::text, computed_at, decel_streak,
               decelerating, price_down_3d
          FROM volume_snapshots
         WHERE ticker = 'SPY' AND (%s::text IS NULL OR snapshot_date::text > %s)
         ORDER BY snapshot_date
        """,
        (since, since),
    )
    out = Extract()
    for key, computed_at, streak, decel, down3 in cur.fetchall():
        if down3 is True and decel is False:
            out.rows.append(Row(key, computed_at, -1.0))
        elif streak is not None:
            out.rows.append(Row(key, computed_at, float(min(streak, DECEL_CAP))))
        else:
            out.skips.append((key, "NULL decel_streak and not distribution"))
    return out


def extract_corr60(cur, since) -> Extract:
    cur.execute(
        """
        SELECT snapshot_date::text, computed_at, avg_pairwise_corr
          FROM diversification_snapshots
         WHERE window_days = 60 AND universe = 'sector_spdr'
           AND (%s::text IS NULL OR snapshot_date::text > %s)
         ORDER BY snapshot_date
        """,
        (since, since),
    )
    out = Extract()
    for key, computed_at, corr in cur.fetchall():
        if corr is None:
            out.skips.append((key, "NULL avg_pairwise_corr"))
        else:
            out.rows.append(Row(key, computed_at, _num(corr)))
    return out


def extract_roro(cur, since) -> Extract:
    """rs_snapshots HYG vs TLT: the STORED 10-session ROC of the HYG/TLT
    ratio (rs_trade). The ratio LEVEL is not stored anywhere, so the stored
    ROC is exported instead (operator decision 3)."""
    cur.execute(
        """
        SELECT snapshot_date::text, computed_at, rs_trade
          FROM rs_snapshots
         WHERE ticker = 'HYG' AND benchmark = 'TLT'
           AND (%s::text IS NULL OR snapshot_date::text > %s)
         ORDER BY snapshot_date
        """,
        (since, since),
    )
    out = Extract()
    for key, computed_at, roc in cur.fetchall():
        if roc is None:
            out.skips.append((key, "NULL rs_trade"))
        else:
            out.rows.append(Row(key, computed_at, _num(roc)))
    return out


def quad_extractor(column: str):
    """quad_regime_history: 'Quad N' -> N, known_at = effective_at (decision
    7), source_key = effective_at::text (event stream, not daily)."""
    assert column in ("monthly_quad", "quarterly_quad")
    def extract(cur, since) -> Extract:
        cur.execute(
            f"""
            SELECT effective_at::text AS src_key, effective_at AS eff, {column}
              FROM quad_regime_history
             WHERE (%s::text IS NULL OR effective_at::text > %s)
             ORDER BY eff
            """,
            (since, since),
        )
        out = Extract()
        for key, effective_at, raw in cur.fetchall():
            if raw is None:
                out.skips.append((key, f"NULL {column}"))
                continue
            digits = [c for c in str(raw) if c.isdigit()]
            if len(digits) != 1 or digits[0] not in "1234":
                raise ValueError(f"unparseable quad value {raw!r} at {key}")
            out.rows.append(Row(key, effective_at, float(digits[0])))
        # Event stream: a burst of writes inside one NY minute (e.g. the
        # 2026-06-20 13:45:23/25/26 dashboard-scrape triple) is one event —
        # keep the LAST row of each minute (matches groupingMethod=last),
        # loudly skip the superseded ones. Daily extractors keep the hard
        # minute-collision failure; there a collision means corruption.
        collapsed: list[Row] = []
        for r in out.rows:
            if collapsed and _ny_minute(collapsed[-1].known_at) == _ny_minute(r.known_at):
                out.skips.append((collapsed[-1].source_key,
                                  f"superseded within same minute by {r.source_key}"))
                collapsed[-1] = r
            else:
                collapsed.append(r)
        out.rows = collapsed
        return out
    return extract


# ─────────────────────────── symbol registry ───────────────────────────
#
# bounds: (lo, hi) inclusive sanity window, hard failure outside it.
# Level symbols get lo > 0 per the spec's "levels > 0" rule.

@dataclass(frozen=True)
class Symbol:
    name: str
    extract: object
    bounds: tuple[float, float]


# ─────────── TradingView history union (2018→, migration 085) ───────────
#
# tv_mfr_history / tv_features_history hold the 8-year MFR indicator history
# imported from TradingView chart exports (tradingview_ingest.py /
# features_backfill.py). Union rule: historical rows for bar_dates BEFORE the
# live source's first row, live rows after — a bar never has two sources.
# Historical known_at is stored in the tables (bar_date 09:30 ET for
# range-derived values, 16:00 ET for close-window features; Step 0 timing
# evidence, 2026-09-06). Hist rows at/after the live boundary are silently
# dropped (expected overlap), not loud-skipped.

# Live-side range position (operator decisions, 2026-09-06 parts 2+3):
#   rp[D] = (close[D-1] - range_low[D]) / (range_high[D] - range_low[D]),
#   clamped [-0.5, 1.5],
# where close[D-1] is the PRIOR SESSION close. Historical rows use the
# TradingView bars (tv_mfr_history.close); LIVE rows use the bot's own
# eod_close_store (migration 087) — unadjusted daily closes this job
# refreshes itself every real run, so RP continues daily with NO CSV
# dependency. Store-vs-TV validation over the 86-session overlap
# (2026-09-06): SPY/UUP/USO exact (max 0.00001%, incl. through SPY's June
# ex-div — no adjustment drift); documented vendor-precision drift only:
# ^VIX one penny of index on most dates (yf settlement print vs TVC,
# <=0.0701%), AAAU half-cent on 9 dates (TV sub-penny closes vs yf 2dp,
# <=0.0126%) — rp impact <=0.002 on both.
# Range low/high come from the live feed's stored row for D.
# NOT MFR's published positionOnRange: on rows the fetcher re-bumps intraday
# that value reflects the price at fetch time, so the definition would drift
# across the history/live splice. A live bar with no stored close in the
# prior RP_MAX_CLOSE_AGE_D calendar days is skipped loudly (never
# approximated with a stale close).
RP_MAX_CLOSE_AGE_D = 4   # Fri close still serves Tue-after-long-weekend
RP_CLOSE_LOOKBACK = "15d"  # refresh window: re-upserting heals late settles


def _clamp_rp(v: float) -> float:
    return max(-0.5, min(1.5, v))


def refresh_eod_close_store(tickers: list[str], period: str = RP_CLOSE_LOOKBACK) -> int:
    """Producer step: upsert the last `period` of UNADJUSTED daily closes for
    `tickers` into eod_close_store. auto_adjust=False — raw exchange closes,
    matching the TV bars the historical rows used; re-upserting past dates
    only ever heals a late-settling print (unadjusted closes never move
    again). A bar dated today is kept only after the 16:00 ET close so a
    stray intraday run cannot bank a forming bar. Best-effort: failures are
    loud, the run continues on whatever the store already holds (the
    freshness guard catches true staleness). Returns rows upserted."""
    try:
        import yfinance as yf
        from price_monitor import HEDGEYE_TO_YFINANCE
    except Exception as e:
        print(f"WARNING: close-store refresh unavailable ({e}) — "
              f"running on stored closes")
        return 0
    sym_of = {t: HEDGEYE_TO_YFINANCE.get(t, t) for t in tickers}
    now_ny = datetime.now(NY)
    today_ny = now_ny.date()
    market_closed = (now_ny.hour, now_ny.minute) >= (16, 5)
    rows = []
    try:
        df = yf.download(list(set(sym_of.values())), period=period,
                         interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
        for t, sym in sym_of.items():
            try:
                sub = df[sym] if len(set(sym_of.values())) > 1 else df
                for idx, c in zip(sub.index, sub["Close"].tolist()):
                    if c != c:          # NaN
                        continue
                    d = idx.date() if hasattr(idx, "date") else idx
                    if d > today_ny or (d == today_ny and not market_closed):
                        continue        # never bank a forming bar
                    rows.append((t, d, float(c)))
            except Exception:
                print(f"WARNING: close-store refresh got no bars for {t} ({sym})")
    except Exception as e:
        print(f"WARNING: close-store download failed ({e}) — "
              f"running on stored closes")
        return 0
    if not rows:
        return 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for t, d, c in rows:
            cur.execute(
                """
                INSERT INTO eod_close_store (ticker, bar_date, close, fetched_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (ticker, bar_date) DO UPDATE
                    SET close = EXCLUDED.close, fetched_at = NOW()
                """,
                (t, d, c))
        conn.commit()
    return len(rows)


def rp_live_from_stored_close(ticker: str):
    """Live RP rows: feed range for bar D + latest earlier close from
    eod_close_store. known_at = fetched_at (the stored row's write time),
    as for every mfr_snapshots-sourced symbol."""
    def extract(cur, since) -> Extract:
        cur.execute(
            """
            SELECT m.snapshot_date::text, m.snapshot_date, m.fetched_at,
                   m.range_low, m.range_high, pc.close, pc.bar_date
              FROM mfr_snapshots m
              LEFT JOIN LATERAL (
                   SELECT close, bar_date FROM eod_close_store e
                    WHERE e.ticker = m.ticker AND e.bar_date < m.snapshot_date
                    ORDER BY e.bar_date DESC LIMIT 1) pc ON TRUE
             WHERE m.ticker = %s AND (%s::text IS NULL OR m.snapshot_date::text > %s)
             ORDER BY m.snapshot_date
            """,
            (ticker, since, since),
        )
        out = Extract()
        for key, snap_date, fetched_at, rl, rh, pclose, pdate in cur.fetchall():
            if rl is None or rh is None:
                out.skips.append((key, "NULL range in source"))
                continue
            if pclose is None:
                out.skips.append((key, "no stored close before bar"))
                continue
            age = (snap_date - pdate).days
            if age > RP_MAX_CLOSE_AGE_D:
                out.skips.append((key, f"prior stored close stale ({age}d, {pdate})"))
                continue
            rl, rh, pclose = _num(rl), _num(rh), _num(pclose)
            out.rows.append(Row(key, fetched_at, _clamp_rp((pclose - rl) / (rh - rl))))
        return out
    return extract


def tv_hist_column(ticker: str, col: str):
    """Extractor over tv_mfr_history: one column, known_at as stored,
    source_key = bar_date. NULLs (trend_tag on the first post-warm-up bar)
    are loud skips."""
    def extract(cur, since) -> Extract:
        cur.execute(
            f"""
            SELECT bar_date::text, known_at, {col}
              FROM tv_mfr_history
             WHERE ticker = %s AND (%s::text IS NULL OR bar_date::text > %s)
             ORDER BY bar_date
            """,
            (ticker, since, since),
        )
        out = Extract()
        for key, known_at, raw in cur.fetchall():
            if raw is None:
                out.skips.append((key, "NULL in source"))
            else:
                out.rows.append(Row(key, known_at, _num(raw)))
        return out
    return extract


def tv_hist_feature(ticker: str, feature: str):
    """Extractor over tv_features_history (features_backfill.py output)."""
    def extract(cur, since) -> Extract:
        cur.execute(
            """
            SELECT bar_date::text, known_at, value
              FROM tv_features_history
             WHERE ticker = %s AND feature = %s
               AND (%s::text IS NULL OR bar_date::text > %s)
             ORDER BY bar_date
            """,
            (ticker, feature, since, since),
        )
        out = Extract()
        for key, known_at, raw in cur.fetchall():
            out.rows.append(Row(key, known_at, _num(raw)))
        return out
    return extract


def shadow_hurst_live(ticker: str):
    """shadow_snapshots.shadow_hurst — the bot's own R/S Hurst, live rows."""
    def extract(cur, since) -> Extract:
        cur.execute(
            """
            SELECT snapshot_date::text, computed_at, shadow_hurst
              FROM shadow_snapshots
             WHERE ticker = %s AND status = 'ok' AND shadow_hurst IS NOT NULL
               AND (%s::text IS NULL OR snapshot_date::text > %s)
             ORDER BY snapshot_date
            """,
            (ticker, since, since),
        )
        out = Extract()
        for key, computed_at, raw in cur.fetchall():
            out.rows.append(Row(key, computed_at, _num(raw)))
        return out
    return extract


def corr_pair_live(a: str, b: str, window: int):
    """correlation_snapshots — tools.relative_strength's live pairwise corr."""
    def extract(cur, since) -> Extract:
        cur.execute(
            """
            SELECT snapshot_date::text, computed_at, correlation
              FROM correlation_snapshots
             WHERE ticker_a = %s AND ticker_b = %s AND window_days = %s
               AND (%s::text IS NULL OR snapshot_date::text > %s)
             ORDER BY snapshot_date
            """,
            (a, b, window, since, since),
        )
        out = Extract()
        for key, computed_at, raw in cur.fetchall():
            if raw is None:
                out.skips.append((key, "NULL correlation"))
            else:
                out.rows.append(Row(key, computed_at, _num(raw)))
        return out
    return extract


def union_hist_live(hist, live, boundary_sql: str, boundary_params: tuple):
    """History before the live source's first row, live rows after.
    boundary_sql must return the first live source_key (ISO date) or NULL;
    with no live rows yet the full history exports."""
    def extract(cur, since) -> Extract:
        cur.execute(boundary_sql, boundary_params)
        row = cur.fetchone()
        boundary = str(row[0]) if row and row[0] is not None else None
        h = hist(cur, since)
        out = Extract()
        if boundary is None:
            out.rows, out.skips = h.rows, h.skips
            return out
        l = live(cur, since)
        out.rows = [r for r in h.rows if r.source_key < boundary] + l.rows
        out.skips = [s for s in h.skips if s[0] < boundary] + l.skips
        return out
    return extract


def _mfr_first_live(ticker: str, cond: str):
    return (f"SELECT min(snapshot_date)::text FROM mfr_snapshots "
            f"WHERE ticker = %s AND {cond}", (ticker,))


# (mfr ticker, symbol tag) — assets whose range symbols SPLICE TV history
# with the live feed.
TV_TICKERS = [("SPY", "SPY"), ("UUP", "UUP"), ("^VIX", "VIX"),
              ("USO", "USO"), ("AAAU", "AAAU")]

# TLT (operator waiver, 2026-09-06): the feed's TLT range diverges from the
# TV indicator on 53/81 overlap dates (0.07-0.29%), so TLT's TrendSpider
# range symbols are TV-INDICATOR-SOURCED END TO END — history AND live
# period, NO mfr_snapshots union — keeping train and live on one
# definition. Rows are flagged source=tv_indicator / feed_verified=false at
# staging (migration 088). The trading desk (REPORT/SCREEN) keeps the feed
# as TLT's source of truth; this waiver is TrendSpider-only.
TV_ONLY_TICKERS = [("TLT", "TLT")]
TLT_TV_ONLY_RANGE_SYMBOLS = [
    f"#MFR_TLT_{s}" for s in ("LO", "HI", "TREND", "RP", "LTLO", "LTHI",
                              "LTRP", "BULL", "BEAR", "BULLDIST")]

# Round-2 full-indicator features (tradingview_ingest_full.py):
# (symbol suffix, tv_features_history feature, bounds). Notes: vixfix is
# exported with its native NEGATIVE sign; TRADE2/TREND2 are OPAQUE
# volatility-like metrics (NOT duration counters — Step 0, 2026-09-06);
# BUY is a real fire flag (22-84 fires per ticker, not all zeros).
FULL_EXPORT_FEATURES = [
    ("HURST64",  "hurst64",    (0.0, 1.0)),
    ("HURST256", "hurst256",   (0.0, 1.0)),
    ("TRENDLVL", "trend_lvl",  (1e-9, 1e6)),
    ("TRADELVL", "trade_lvl",  (1e-9, 1e6)),
    ("BUY",      "buy",        (0.0, 1.0)),
    ("MEGABUY",  "mega_buy",   (0.0, 1.0)),
    ("SELL",     "sell",       (0.0, 1.0)),
    ("MEGASELL", "mega_sell",  (0.0, 1.0)),
    ("VOLAT",    "volatility", (0.0, 100.0)),
    ("VIXFIX",   "vixfix",     (-400.0, 0.0)),
    ("UPT1",     "up_t1",      (1e-9, 1e6)),
    ("DNT1",     "down_t1",    (-100.0, 1e6)),
    ("TRADE2",   "trade2",     (0.0, 100.0)),
    ("TREND2",   "trend2",     (0.0, 100.0)),
]
FULL_TICKERS = [("SPY", "SPY"), ("UUP", "UUP"), ("USO", "USO"),
                ("AAAU", "AAAU"), ("TLT", "TLT")]


def _tv_symbols() -> list["Symbol"]:
    syms: list[Symbol] = []
    for ticker, tag in TV_TICKERS:
        px_hi = 1e4 if ticker == "^VIX" else 1e6
        rng_b = _mfr_first_live(ticker, "range_low IS NOT NULL AND range_high IS NOT NULL")
        syms += [
            Symbol(f"#MFR_{tag}_LO",
                   union_hist_live(tv_hist_column(ticker, "range_low"),
                                   mfr_column(ticker, "range_low"), *rng_b),
                   (1e-9, px_hi)),
            Symbol(f"#MFR_{tag}_HI",
                   union_hist_live(tv_hist_column(ticker, "range_high"),
                                   mfr_column(ticker, "range_high"), *rng_b),
                   (1e-9, px_hi)),
            Symbol(f"#MFR_{tag}_TREND",
                   union_hist_live(tv_hist_column(ticker, "trend_tag"),
                                   mfr_column(ticker, "trend_signal", _convert_trend),
                                   *_mfr_first_live(ticker, "trend_signal IS NOT NULL")),
                   (-1, 1)),
            # rp[D] = (close[D-1] - range_low[D]) / (range_high[D] - range_low[D]),
            # clamped [-0.5, 1.5]; close[D-1] = prior session close — TV bars
            # on the historical side (features_backfill), the bot's own
            # eod_close_store on the live side (rp_live_from_stored_close;
            # validated against TV <=0.01% over the overlap, 2026-09-06).
            # Ranges: TV before the boundary, the live feed's stored row after.
            Symbol(f"#MFR_{tag}_RP",
                   union_hist_live(tv_hist_feature(ticker, "rp"),
                                   rp_live_from_stored_close(ticker), *rng_b),
                   (-0.5, 1.5)),
            # LT band + trend levels: TV history only. The feed's
            # ltRangeData.upperRange matches TV exactly but lowerRange drifts
            # ~0.12% (checked 2026-09-06) — no live splice until reconciled;
            # bull/bear levels are not in the feed at all.
            # VIX's LT low goes negative on spike unwinds (min -14.67,
            # 2020-03) — the indicator's band, exported as-is.
            Symbol(f"#MFR_{tag}_LTLO", tv_hist_column(ticker, "lt_range_low"),
                   (-100.0 if ticker == "^VIX" else 1e-9, px_hi)),
            Symbol(f"#MFR_{tag}_LTHI", tv_hist_column(ticker, "lt_range_high"), (1e-9, px_hi)),
            Symbol(f"#MFR_{tag}_LTRP", tv_hist_feature(ticker, "lt_rp"), (-0.5, 1.5)),
            Symbol(f"#MFR_{tag}_BULL", tv_hist_column(ticker, "bull_level"), (1e-9, px_hi)),
            Symbol(f"#MFR_{tag}_BEAR", tv_hist_column(ticker, "bear_level"), (1e-9, px_hi)),
            Symbol(f"#MFR_{tag}_BULLDIST", tv_hist_feature(ticker, "bull_dist"), (-3.0, 1.0)),
            Symbol(f"#SHADOW_{tag}_HURST",
                   union_hist_live(tv_hist_feature(ticker, "shadow_hurst"),
                                   shadow_hurst_live(ticker),
                                   "SELECT min(snapshot_date)::text FROM shadow_snapshots "
                                   "WHERE ticker = %s AND status = 'ok' "
                                   "AND shadow_hurst IS NOT NULL", (ticker,)),
                   (0.0, 1.0)),
        ]
    syms.append(Symbol(
        "#CORR_SPY_UUP60",
        union_hist_live(tv_hist_feature("UUP", "corr60_spy"),
                        corr_pair_live("UUP", "SPY", 60),
                        "SELECT min(snapshot_date)::text FROM correlation_snapshots "
                        "WHERE ticker_a = 'UUP' AND ticker_b = 'SPY' "
                        "AND window_days = 60", ()),
        (-1, 1)))
    # Macro Show "Key $USD Correlations" windows (30/90d; 15/120/180d = Round
    # 2). TV history only — no live module computes these pairs/windows yet.
    for sym_name, feat in [("#CORR_UUP_SPY30", "corr30_spy"),
                           ("#CORR_UUP_SPY90", "corr90_spy"),
                           ("#CORR_UUP_USO30", "corr30_uso"),
                           ("#CORR_UUP_USO90", "corr90_uso"),
                           ("#CORR_UUP_AAAU30", "corr30_aaau"),
                           ("#CORR_UUP_AAAU90", "corr90_aaau")]:
        syms.append(Symbol(sym_name, tv_hist_feature("UUP", feat), (-1, 1)))
    # TLT: TV-only range set (waiver — see TV_ONLY_TICKERS). Everything from
    # tv_mfr_history / tv_features_history, including the live period; only
    # #SHADOW_TLT_HURST splices to shadow_snapshots (the bot's OWN hurst,
    # one definition on both sides — not a feed value).
    for ticker, tag in TV_ONLY_TICKERS:
        syms += [
            Symbol(f"#MFR_{tag}_LO", tv_hist_column(ticker, "range_low"), (1e-9, 1e6)),
            Symbol(f"#MFR_{tag}_HI", tv_hist_column(ticker, "range_high"), (1e-9, 1e6)),
            Symbol(f"#MFR_{tag}_TREND", tv_hist_column(ticker, "trend_tag"), (-1, 1)),
            Symbol(f"#MFR_{tag}_RP", tv_hist_feature(ticker, "rp"), (-0.5, 1.5)),
            Symbol(f"#MFR_{tag}_LTLO", tv_hist_column(ticker, "lt_range_low"), (1e-9, 1e6)),
            Symbol(f"#MFR_{tag}_LTHI", tv_hist_column(ticker, "lt_range_high"), (1e-9, 1e6)),
            Symbol(f"#MFR_{tag}_LTRP", tv_hist_feature(ticker, "lt_rp"), (-0.5, 1.5)),
            Symbol(f"#MFR_{tag}_BULL", tv_hist_column(ticker, "bull_level"), (1e-9, 1e6)),
            Symbol(f"#MFR_{tag}_BEAR", tv_hist_column(ticker, "bear_level"), (1e-9, 1e6)),
            Symbol(f"#MFR_{tag}_BULLDIST", tv_hist_feature(ticker, "bull_dist"), (-3.0, 1.0)),
            Symbol(f"#SHADOW_{tag}_HURST",
                   union_hist_live(tv_hist_feature(ticker, "shadow_hurst"),
                                   shadow_hurst_live(ticker),
                                   "SELECT min(snapshot_date)::text FROM shadow_snapshots "
                                   "WHERE ticker = %s AND status = 'ok' "
                                   "AND shadow_hurst IS NOT NULL", (ticker,)),
                   (0.0, 1.0)),
        ]
    # Round-2 full-indicator feature symbols, five assets.
    for ticker, tag in FULL_TICKERS:
        for suffix, feat, bounds in FULL_EXPORT_FEATURES:
            syms.append(Symbol(f"#MFR_{tag}_{suffix}",
                               tv_hist_feature(ticker, feat), bounds))
    return syms


SYMBOLS: list[Symbol] = [
    Symbol("#MFR_SPY_HURST", mfr_column("SPY", "hurst"),                (1e-9, 1.0)),
    Symbol("#MFR_SPY_IVPD",  mfr_column("SPY", "(full_payload->>'ivpd')::numeric"), (-10, 10)),
    Symbol("#MFR_SPY_ZG",    mfr_column("SPY", "zero_gamma"),           (1e-9, 1e6)),
    Symbol("#MFR_SPY_CW",    mfr_column("SPY", "call_wall_mfr"),        (1e-9, 1e6)),
    Symbol("#MFR_SPY_PW",    mfr_column("SPY", "put_wall_mfr"),         (1e-9, 1e6)),
    Symbol("#MFR_SPY_DECEL", extract_decel,                             (-1, DECEL_CAP)),
    Symbol("#RS_SPYUNIV_CORR60", extract_corr60,                        (-1, 1)),
    Symbol("#RORO_HYGTLT_ROC",   extract_roro,                          (-1, 1)),
    Symbol("#QUAD_M", quad_extractor("monthly_quad"),                   (1, 4)),
    Symbol("#QUAD_Q", quad_extractor("quarterly_quad"),                 (1, 4)),
    # #MFR_{SPY,VIX}_LO/_HI/_TREND moved into _tv_symbols(): same symbol
    # names, now TV-history + live-feed unions.
]
SYMBOLS += _tv_symbols()


# ─────────────────────────── validation ───────────────────────────

class ValidationError(Exception):
    pass


def _ny_minute(ts: datetime) -> str:
    return ts.astimezone(NY).strftime("%Y-%m-%dT%H:%M")


def validate(symbol: Symbol, rows: list[Row]) -> None:
    """Hard-fail checks per spec Step 2. Rows arrive ordered by source_key."""
    now = datetime.now(UTC)
    lo, hi = symbol.bounds
    problems = []
    prev_known = None
    seen_minutes: dict[str, str] = {}
    for r in rows:
        if r.value is None or isinstance(r.value, float) and (
                math.isnan(r.value) or math.isinf(r.value)):
            problems.append(f"{r.source_key}: non-finite value {r.value!r}")
            continue
        if not (lo <= r.value <= hi):
            problems.append(f"{r.source_key}: value {r.value} outside [{lo}, {hi}]")
        if r.known_at.tzinfo is None:
            problems.append(f"{r.source_key}: naive known_at {r.known_at}")
            continue
        if r.known_at >= now:
            problems.append(f"{r.source_key}: known_at {r.known_at} not in the past")
        if prev_known is not None and r.known_at < prev_known:
            problems.append(
                f"{r.source_key}: known_at {r.known_at} earlier than previous "
                f"row's {prev_known} (non-monotonic vs source order)")
        prev_known = r.known_at
        minute = _ny_minute(r.known_at)
        if minute in seen_minutes:
            problems.append(
                f"{r.source_key}: NY-minute stamp {minute} collides with "
                f"{seen_minutes[minute]} (groupingMethod=last would drop one)")
        seen_minutes[minute] = r.source_key
    if problems:
        detail = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise ValidationError(f"{symbol.name}: {len(problems)} violation(s):\n  {detail}{more}")


# ─────────────────────────── extraction orchestration ───────────────────────────

def last_uploaded_key(cur, symbol: str):
    cur.execute(
        "SELECT max(source_key) FROM ts_export_log "
        "WHERE symbol = %s AND uploaded_at IS NOT NULL",
        (symbol,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def extract_all(backfill: bool, symbols: list[Symbol]) -> dict[str, Extract]:
    """Run every extractor (read-only) and validate. Returns {symbol: Extract}."""
    results: dict[str, Extract] = {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for sym in symbols:
            since = None if backfill else last_uploaded_key(cur, sym.name)
            ext = sym.extract(cur, since)
            validate(sym, ext.rows)
            results[sym.name] = ext
            for key, reason in ext.skips:
                print(f"  SKIP {sym.name} {key}: {reason}")
    return results


# ─────────────────────────── staging (ts_export_log) ───────────────────────────

def stage_rows(results: dict[str, Extract]) -> int:
    """Upsert extracted rows into ts_export_log. Uploaded rows are never
    touched (decision 5: never re-export an uploaded (symbol, source_key));
    still-pending rows refresh value/known_at. Returns rows staged/refreshed.

    Batched with execute_values: the 8-year backfill stages ~115k rows, and
    one round trip per row over the public DB link held a transaction open
    long enough for the server to drop the connection (2026-09-06 backfill
    run). Commits per batch — the upsert is idempotent, so a mid-run failure
    just re-stages on retry."""
    from psycopg2.extras import execute_values
    rows = [(name, r.source_key, r.known_at, r.value)
            for name, ext in results.items() for r in ext.rows]
    n = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), 1000):
            execute_values(
                cur,
                """
                INSERT INTO ts_export_log (symbol, source_key, known_at, value)
                VALUES %s
                ON CONFLICT (symbol, source_key) DO UPDATE
                    SET value = EXCLUDED.value, known_at = EXCLUDED.known_at
                    WHERE ts_export_log.uploaded_at IS NULL
                """,
                rows[i:i + 1000], page_size=1000)
            n += cur.rowcount
            conn.commit()
    return n


def fetch_pending(symbols: list[str]) -> list[tuple[int, str, datetime, float]]:
    """(id, symbol, known_at, value) for every staged-but-not-uploaded row of
    the given symbols, ordered for the CSV."""
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, known_at, value
              FROM ts_export_log
             WHERE uploaded_at IS NULL AND symbol = ANY(%s)
             ORDER BY symbol, known_at
            """,
            (symbols,),
        )
        return cur.fetchall()


# ─────────────────────────── CSV + upload ───────────────────────────

def _fmt_value(v: float) -> str:
    """Plain decimal, never scientific notation. %.10g renders |v| < 1e-4 as
    '8e-05', which TrendSpider's CSV ingest does not parse — the one such row
    in #CORR_SPY_UUP60 left the whole symbol with no chart data despite an
    HTTP 200 (2026-09-06). Seven symbols carried at least one e-notation row;
    all were re-uploaded plain-decimal."""
    s = f"{v:.10g}"
    if "e" in s or "E" in s:
        s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s or "0"


def build_csv(rows: list[tuple[int, str, datetime, float]]) -> str:
    """3-column CSV, no header: #SYMBOL,YYYY-MM-DDTHH:mm,value (NY wall clock)."""
    lines = [f"{sym},{_ny_minute(known_at)},{_fmt_value(val)}"
             for _id, sym, known_at, val in rows]
    return "\n".join(lines) + "\n"


def upload_csv(csv_text: str, url: str, api_key: str, symbol: str) -> tuple[int, str]:
    """POST one single-symbol CSV to the TrendSpider custom-symbols endpoint.
    Returns (http_status, response_body).

    The JSON body MUST be serialized with compact separators: TrendSpider's
    backend hangs ~60s (-> nginx 504) on JSON containing whitespace after
    ':' or ',' — json.dumps' DEFAULT separators. Isolated 2026-09-03 by
    byte-diffing failing vs succeeding requests; same payload compacted
    returns 200 in 0.2s. requests' json= kwarg uses the default separators,
    so serialize explicitly and send via data=."""
    b64 = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")  # no newlines
    payload = json.dumps({
        "symbol": symbol,
        "fileBase64": "data:text/csv;base64," + b64,
        "targetAssetType": "stock",
        "groupingMethod": "last",
    }, separators=(",", ":"))
    import requests
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-type": "application/json",
        },
        data=payload,
        timeout=120,
    )
    return resp.status_code, resp.text


def mark_batch(row_ids: list[int], batch_id: str, status: int, ok: bool) -> None:
    """Record the upload result. 2xx: uploaded_at set. Non-2xx: http_status
    recorded, uploaded_at stays NULL so the rows retry next run."""
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        if ok:
            cur.execute(
                "UPDATE ts_export_log SET uploaded_at = NOW(), "
                "http_status = %s, batch_id = %s WHERE id = ANY(%s)",
                (status, batch_id, row_ids),
            )
        else:
            cur.execute(
                "UPDATE ts_export_log SET http_status = %s, batch_id = %s "
                "WHERE id = ANY(%s)",
                (status, batch_id, row_ids),
            )
        conn.commit()


# ─────────────────────────── report helpers ───────────────────────────

def print_summary(results: dict[str, Extract], symbols: list[Symbol]) -> None:
    print(f"\n{'symbol':<22} {'rows':>5} {'skips':>5}  known_at range"
          f"{'':<28} value range")
    for sym in symbols:
        ext = results[sym.name]
        if ext.rows:
            ka_min = _ny_minute(min(r.known_at for r in ext.rows))
            ka_max = _ny_minute(max(r.known_at for r in ext.rows))
            v_min = min(r.value for r in ext.rows)
            v_max = max(r.value for r in ext.rows)
            print(f"{sym.name:<22} {len(ext.rows):>5} {len(ext.skips):>5}  "
                  f"{ka_min} .. {ka_max}  {_fmt_value(v_min)} .. {_fmt_value(v_max)}")
        else:
            print(f"{sym.name:<22} {0:>5} {len(ext.skips):>5}  (no new rows)")


# ─────────────────────────── main ───────────────────────────

def run(dry_run: bool, backfill: bool,
        symbols: list[Symbol] | None = None) -> int:
    symbols = SYMBOLS if symbols is None else symbols
    db_pg._load_dotenv_fallback()   # ensure TRENDSPIDER_* reach os.environ on CLI runs
    api_key = os.environ.get("TRENDSPIDER_API_KEY")
    url = os.environ.get("TRENDSPIDER_UPLOAD_URL", DEFAULT_UPLOAD_URL)
    if not dry_run and not api_key:
        print("ERROR: TRENDSPIDER_API_KEY not set (.env)")
        return 2

    mode = f"{'DRY-RUN ' if dry_run else ''}{'backfill' if backfill else 'incremental'}"
    print(f"TS EXPORT — {mode} — {len(symbols)} symbols")

    if not dry_run:
        # producer step: keep eod_close_store current so live RP needs no
        # fresh TV CSV (dry runs stay write-free and read stored closes).
        n_closes = refresh_eod_close_store([t for t, _tag in TV_TICKERS])
        print(f"eod_close_store: {n_closes} close rows refreshed")

    results = extract_all(backfill, symbols)
    print_summary(results, symbols)
    total_rows = sum(len(e.rows) for e in results.values())

    if dry_run:
        DRY_RUN_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(NY).strftime("%Y%m%dT%H%M%S")
        n_files = 0
        for sym in symbols:
            rows = [(0, sym.name, r.known_at, r.value)
                    for r in results[sym.name].rows]
            if not rows:
                continue
            csv_text = build_csv(rows)
            path = DRY_RUN_DIR / f"ts_export_{stamp}_{sym.name.lstrip('#')}.csv"
            path.write_text(csv_text, encoding="utf-8", newline="\n")
            size = len(csv_text.encode("utf-8"))
            if size > MAX_CSV_BYTES:
                raise ValidationError(f"{sym.name} CSV is {size} bytes > 7MB cap")
            n_files += 1
            print(f"{sym.name}: {len(rows)} rows, {size} bytes -> {path}")
        print(f"\nDRY RUN complete — {total_rows} rows across {n_files} "
              f"per-symbol files. Nothing staged, nothing uploaded.")
        return 0

    staged = stage_rows(results)
    print(f"\nstaged/refreshed {staged} rows in ts_export_log")

    # Provenance flags (migration 088): TLT range rows are TV-indicator-
    # sourced and never verified against the live feed (operator waiver).
    flagged = [s for s in TLT_TV_ONLY_RANGE_SYMBOLS
               if any(sym.name == s for sym in symbols)]
    if flagged:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ts_export_log SET source = 'tv_indicator', "
                "feed_verified = false WHERE symbol = ANY(%s) "
                "AND (source IS DISTINCT FROM 'tv_indicator' "
                "OR feed_verified IS DISTINCT FROM false)",
                (flagged,))
            n_flag = cur.rowcount
            conn.commit()
        print(f"flagged {n_flag} TLT range rows source=tv_indicator, "
              f"feed_verified=false")

    batch_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    n_posts = 0
    n_uploaded_rows = 0
    failures: list[str] = []
    for sym in symbols:
        pending = fetch_pending([sym.name])
        if not pending:
            print(f"{sym.name}: nothing pending")
            continue
        csv_text = build_csv(pending)
        size = len(csv_text.encode("utf-8"))
        if size > MAX_CSV_BYTES:
            raise ValidationError(f"{sym.name} CSV is {size} bytes > 7MB cap")
        batch_id = f"{batch_stamp}-{sym.name.lstrip('#')}"
        if n_posts > 0:
            time.sleep(SLEEP_BETWEEN_UPLOADS_S)
        status, body = upload_csv(csv_text, url, api_key, sym.name)
        ok = 200 <= status < 300
        mark_batch([r[0] for r in pending], batch_id, status, ok)
        n_posts += 1
        if ok:
            n_uploaded_rows += len(pending)
            print(f"{sym.name}: HTTP {status}, {len(pending)} rows uploaded")
        else:
            failures.append(f"{sym.name}: HTTP {status}")
            print(f"{sym.name}: HTTP {status} — NOT marked uploaded.")
            print(f"  response body: {body[:500]}")

    n_symbols = sum(1 for e in results.values() if e.rows)
    if failures:
        summary = (f"TS EXPORT FAILED · {'; '.join(failures)} · "
                   f"{n_uploaded_rows}/{total_rows} rows uploaded")
    else:
        summary = (f"TS EXPORT ok · {n_symbols} symbols · {n_uploaded_rows} rows · "
                   f"{n_posts} posts")
    print(f"\n{summary}")
    try:
        from notifier import send_telegram
        send_telegram("TS EXPORT", summary)
    except Exception as e:
        print(f"WARNING: telegram summary failed: {e}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TrendSpider custom-symbol export")
    ap.add_argument("--dry-run", action="store_true",
                    help="write CSVs to ./ts_export/ and print counts; no DB writes, no upload")
    ap.add_argument("--backfill", action="store_true",
                    help="export full history (default: incremental past last uploaded row)")
    ap.add_argument("--only", metavar="SYMS",
                    help="comma-separated symbol names: run ONLY these")
    ap.add_argument("--exclude", metavar="SYMS",
                    help="comma-separated symbol names to leave out")
    args = ap.parse_args()

    symbols = list(SYMBOLS)
    for flag, keep in (("only", True), ("exclude", False)):
        raw = getattr(args, flag)
        if not raw:
            continue
        names = {s.strip() for s in raw.split(",") if s.strip()}
        unknown = names - {s.name for s in SYMBOLS}
        if unknown:
            print(f"ERROR: --{flag} names not in the registry: {sorted(unknown)}")
            return 2
        symbols = [s for s in symbols if (s.name in names) is keep]
    if not symbols:
        print("ERROR: symbol filter left nothing to run")
        return 2

    try:
        return run(dry_run=args.dry_run, backfill=args.backfill, symbols=symbols)
    except ValidationError as e:
        msg = f"TS EXPORT FAILED · validation: {e}"
        print(f"\nERROR: {msg}")
        if not args.dry_run:
            try:
                from notifier import send_telegram
                send_telegram("TS EXPORT", msg[:1000])
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
