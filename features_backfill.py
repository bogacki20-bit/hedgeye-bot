"""features_backfill.py — recompute bar-derived features from the TradingView
daily bars (data/tradingview/*.csv) into tv_features_history (migration 085).

Only features a live module also computes, with the SAME functions:

  rp           (close[D-1] - range_low[D]) / (range_high[D] - range_low[D]),
               clamped [-0.5, 1.5]. Matches MFR's published
               rangeData.positionOnRange (verified 2026-09-04: MFR uses the
               prior close against the published range). known_at 09:30 ET.
  lt_rp        same, LT range. known_at 09:30 ET.
  bull_dist    (close[D-1] - bull_level[D]) / close[D-1]. known_at 09:30 ET.
  shadow_hurst shadow_range.hurst_rs on the trailing 126 TV closes — the
               bot's own R/S Hurst (shadow_snapshots.shadow_hurst live).
               NOT MFR's published hurst. known_at 16:00 ET.
  corrNN_x     Pearson correlation of daily returns over the trailing NN
               sessions, tools.relative_strength.pearson/returns — the same
               function that writes correlation_snapshots live. Stored under
               ticker UUP: corr60_spy (#CORR_SPY_UUP60), corr30_spy,
               corr90_spy, corr30_uso, corr90_uso, corr30_aaau, corr90_aaau
               (Macro Show "Key $USD Correlations" windows; 15/120/180d are
               Round 2). known_at 16:00 ET.

Excluded per Step-0 review (2026-09-06): decel/distribution (TV BATS volume
is not the live yfinance volume; ported to TrendSpider JS instead), MFR's own
hurst (proprietary, not reproducible), SPY-USO / SPY-AAAU pair corr at 60d
(no live module computes them; the 30/90 UUP pairs above are deck-defined
process features, operator decision 2026-09-06).

Overlap diff gates (printed every run): shadow_hurst vs shadow_snapshots,
corr60_spy vs correlation_snapshots UUP-SPY w=60, rp vs the live feed's
published positionOnRange.

CLI:
    py features_backfill.py            # compute + upsert + diff report
    py features_backfill.py --dry-run  # compute + diff report only
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import db_pg  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402
from shadow_range import hurst_rs  # noqa: E402
from tools.relative_strength import pearson, returns  # noqa: E402
from tradingview_ingest import FILE_TICKERS, TV_DIR, load_csv  # noqa: E402

NY = ZoneInfo("America/New_York")

HURST_WINDOW = 126        # RangeParams.hurst_window — shadow engine's lookback
RP_CLAMP = (-0.5, 1.5)
CORR_PAIRS = [            # (feature suffix, other ticker, window)
    ("corr60_spy", "SPY", 60),
    ("corr30_spy", "SPY", 30),
    ("corr90_spy", "SPY", 90),
    ("corr30_uso", "USO", 30),
    ("corr90_uso", "USO", 90),
    ("corr30_aaau", "AAAU", 30),
    ("corr90_aaau", "AAAU", 90),
]


def _at(d, h, m):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def load_all():
    """{ticker: (all_rows, body_rows)} for every mapped CSV present."""
    out = {}
    for fname, ticker in FILE_TICKERS.items():
        p = TV_DIR / fname
        if p.exists():
            rows, body, _ = load_csv(p)
            out[ticker] = (rows, body)
    return out


def range_features(ticker, rows, body):
    """(ticker, bar_date, known_at, feature, value) rows for rp / lt_rp /
    bull_dist. close[D-1] comes from the full row list (warm-up rows carry
    real closes), the levels from body row D."""
    idx_all = {r["date"]: i for i, r in enumerate(rows)}
    out = []
    for r in body:
        i = idx_all[r["date"]]
        if i == 0:
            continue
        prev_close = rows[i - 1]["close"]
        if not prev_close:
            continue
        ka = _at(r["date"], 9, 30)
        rp = _clamp((prev_close - r["rl"]) / (r["rh"] - r["rl"]), *RP_CLAMP)
        lt_rp = _clamp((prev_close - r["ltl"]) / (r["lth"] - r["ltl"]), *RP_CLAMP)
        bd = (prev_close - r["bull"]) / prev_close
        out.append((ticker, r["date"], ka, "rp", round(rp, 6)))
        out.append((ticker, r["date"], ka, "lt_rp", round(lt_rp, 6)))
        out.append((ticker, r["date"], ka, "bull_dist", round(bd, 6)))
    return out


def hurst_features(ticker, rows):
    """shadow_hurst per bar with >= HURST_WINDOW trailing closes (warm-up
    closes count — hurst_rs consumes closes only)."""
    closes = [r["close"] for r in rows]
    out = []
    for i, r in enumerate(rows):
        if i + 1 < HURST_WINDOW:
            continue
        h = hurst_rs(np.asarray(closes[i + 1 - HURST_WINDOW: i + 1], dtype=float))
        if math.isnan(h):
            continue
        out.append((ticker, r["date"], _at(r["date"], 16, 0),
                    "shadow_hurst", round(float(h), 6)))
    return out


def corr_features(data):
    """UUP-vs-X trailing-window return correlations, live algorithm: each
    series' closes taken independently up to D, returns, tail-N, pearson."""
    uup_rows = data["UUP"][0]
    out = []
    for feat, other, window in CORR_PAIRS:
        if other not in data:
            continue
        oth_rows = data[other][0]
        oth_idx = {r["date"]: i for i, r in enumerate(oth_rows)}
        u_closes = [r["close"] for r in uup_rows]
        o_closes = [r["close"] for r in oth_rows]
        for i, r in enumerate(uup_rows):
            j = oth_idx.get(r["date"])
            if j is None or i < window + 1 or j < window + 1:
                continue
            ru = returns(u_closes[: i + 1])[-window:]
            ro = returns(o_closes[: j + 1])[-window:]
            c = pearson(ru, ro)
            if c is None:
                continue
            out.append(("UUP", r["date"], _at(r["date"], 16, 0),
                        feat, round(c, 5)))
    return out


# ─────────────────────────── overlap diff gates ───────────────────────────

def _diff_report(label, pairs, tol=0.01):
    if not pairs:
        print(f"  {label}: no live overlap to diff")
        return
    ds = sorted(abs(a - b) for a, b in pairs)
    n = len(ds)
    print(f"  {label}: n={n}, |diff| median {ds[n // 2]:.5f}, max {ds[-1]:.5f}, "
          f"within {tol}: {sum(x <= tol for x in ds)}/{n}")


def diff_gates(cur, rows_by_feature):
    print("\nOverlap diff gates (recomputed vs live module output):")
    # shadow_hurst vs shadow_snapshots
    hurst_rows = rows_by_feature.get("shadow_hurst", [])
    for ticker in sorted({r[0] for r in hurst_rows}):
        cur.execute(
            """SELECT snapshot_date, shadow_hurst FROM shadow_snapshots
                WHERE ticker=%s AND status='ok' AND shadow_hurst IS NOT NULL""",
            (ticker,))
        live = {r[0]: float(r[1]) for r in cur.fetchall()}
        pairs = [(float(v), live[d]) for (t, d, _ka, _f, v) in hurst_rows
                 if t == ticker and d in live]
        _diff_report(f"shadow_hurst {ticker} vs shadow_snapshots", pairs, tol=0.02)
    # corr60_spy vs correlation_snapshots UUP-SPY
    cur.execute(
        """SELECT snapshot_date, correlation FROM correlation_snapshots
            WHERE ticker_a='UUP' AND ticker_b='SPY' AND window_days=60""")
    live = {r[0]: float(r[1]) for r in cur.fetchall()}
    pairs = [(float(v), live[d]) for (_t, d, _ka, f, v)
             in rows_by_feature.get("corr60_spy", []) if d in live]
    _diff_report("corr60_spy vs correlation_snapshots UUP-SPY", pairs)
    # rp vs the feed's published positionOnRange. Only rows the feed fetched
    # BEFORE the bar's 09:30 open qualify: the feed recomputes positionOnRange
    # from latestPrice at fetch time, so intraday-bumped rows carry an
    # intraday price, not the prior close our rp is defined on.
    for ticker in sorted({r[0] for r in rows_by_feature.get("rp", [])}):
        cur.execute(
            """SELECT snapshot_date,
                      COALESCE(mfr_pos_short,
                               (full_payload->'rangeData'->>'positionOnRange')::numeric)
                 FROM mfr_snapshots
                WHERE ticker=%s
                  AND fetched_at < (snapshot_date::timestamp + interval '9 hours 30 minutes')
                                   AT TIME ZONE 'America/New_York'""", (ticker,))
        live = {r[0]: float(r[1]) for r in cur.fetchall() if r[1] is not None}
        pairs = [(float(v), _clamp(live[d], *RP_CLAMP))
                 for (t, d, _ka, _f, v) in rows_by_feature["rp"]
                 if t == ticker and d in live]
        _diff_report(f"rp {ticker} vs feed positionOnRange", pairs, tol=0.02)


def main() -> int:
    ap = argparse.ArgumentParser(description="TV bar-derived features -> tv_features_history")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + diff report only; no DB writes")
    args = ap.parse_args()

    data = load_all()
    print(f"Loaded {len(data)} TV series: {sorted(data)}")

    all_rows = []
    for ticker, (rows, body) in data.items():
        rf = range_features(ticker, rows, body)
        hf = hurst_features(ticker, rows)
        all_rows += rf + hf
        print(f"  {ticker}: {len(rf)} range-feature rows, {len(hf)} hurst rows")
    cf = corr_features(data)
    all_rows += cf
    print(f"  UUP corr pairs: {len(cf)} rows across {len(CORR_PAIRS)} features")
    print(f"Total feature rows: {len(all_rows)}")

    rows_by_feature: dict[str, list] = {}
    for row in all_rows:
        rows_by_feature.setdefault(row[3], []).append(row)
    for feat in sorted(rows_by_feature):
        rs = rows_by_feature[feat]
        vals = [float(r[4]) for r in rs]
        print(f"    {feat:<12} n={len(rs):>5}  {min(r[1] for r in rs)} .. "
              f"{max(r[1] for r in rs)}  value [{min(vals):.5f}, {max(vals):.5f}]")

    with db_pg.get_conn() as conn, conn.cursor() as cur:
        diff_gates(cur, rows_by_feature)
        if args.dry_run:
            print("\n[dry-run] no DB writes.")
            return 0
        execute_values(
            cur,
            """
            INSERT INTO tv_features_history (ticker, bar_date, known_at, feature, value)
            VALUES %s
            ON CONFLICT (ticker, bar_date, feature) DO UPDATE SET
                known_at = EXCLUDED.known_at,
                value = EXCLUDED.value,
                computed_at = NOW()
            """,
            all_rows, page_size=1000)
        conn.commit()
        cur.execute("SELECT feature, count(*) FROM tv_features_history "
                    "GROUP BY feature ORDER BY feature")
        print("\ntv_features_history now holds:")
        for f, n in cur.fetchall():
            print(f"  {f:<12} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
