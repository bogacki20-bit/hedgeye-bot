"""tradingview_ingest_full.py — load the round-2 (45-column full MFR
indicator) TradingView exports into tv_features_history.

    py tradingview_ingest_full.py            # all *_1D_full.csv files
    py tradingview_ingest_full.py --dry-run

Additive pass (operator brief, 2026-09-06): round-1 files and
tv_mfr_history are untouched. The 45-column export carries duplicate
header names (Trend x3, Trade x3, Buy x2, Plot x4), so parsing is
POSITIONAL — see COLS. Ignored by design: the duplicated Trend/Trade
copies (verified identical in Step 0), PlotCandle/Plot columns
(unlabeled), 52-week columns (empty). Loaded but only partially exported
(Round-3 staging): up_t2/down_t2, period_open, period1-3.

Step-0 findings baked into the doctrine here:
  * Every feature is an indicator OUTPUT published the prior evening ->
    known_at = bar_date 09:30 ET, same as the ranges (round-2 ranges match
    round-1 byte-exactly, so the round-1 timing evidence carries over).
  * Warm-up NaNs (AAAU trend_lvl/trend2 pre-2022-10-28, hurst256 first
    ~189 rows, period1-3 first ~33) are SKIPPED per (row, feature), never
    filled.
  * buy is NOT all zeros (22-84 fires per ticker) — loaded and exported.
  * trade2/trend2 are NOT duration counters: non-integer, per-bar series;
    trade2 tracks the Volatility column at r~0.75-0.81 on every ticker,
    trend2 correlates weakly with everything tested. Exported as OPAQUE
    features, no duration interpretation.
  * vixfix is exported with its native NEGATIVE sign ([-347, 0]).
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

NY = ZoneInfo("America/New_York")
TV_DIR = REPO / "data" / "tradingview"

FULL_FILES = {
    "BATS_SPY_1D_full.csv":  "SPY",
    "BATS_UUP_1D_full.csv":  "UUP",
    "BATS_USO_1D_full.csv":  "USO",
    "BATS_AAAU_1D_full.csv": "AAAU",
    "BATS_TLT_1D_full.csv":  "TLT",
}

# feature name -> CSV column index (positional; duplicate header names)
FEATURE_COLS = {
    "trend_lvl": 5, "trade_lvl": 6,
    # "buy" is handled specially: the export scatters the SAME buy plot
    # across the two duplicate Buy columns (9 and 10) — SPY carries it in
    # both, UUP/USO/AAAU/TLT only in column 10 — so buy = max(col9, col10).
    "mega_buy": 11, "sell": 12, "mega_sell": 13,
    "up_t1": 14, "down_t1": 15, "up_t2": 16, "down_t2": 17,
    "period_open": 18,
    "hurst64": 26, "hurst256": 27,
    "period1": 28, "period2": 29, "period3": 30,
    "vixfix": 31, "volatility": 40,
    "trade2": 41, "trend2": 42,
}
N_COLS = 45


def _f(s):
    if s is None or s == "" or s.upper() == "NAN":
        return None
    return float(s)


def load_features(path: Path):
    """[(bar_date, {feature: value-or-None})], oldest first."""
    out = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        if len(header) != N_COLS:
            raise SystemExit(f"{path.name}: {len(header)} columns, expected {N_COLS}")
        for rec in rd:
            d = datetime.fromtimestamp(int(rec[0]), tz=NY).date()
            fv = {k: _f(rec[i]) for k, i in FEATURE_COLS.items()}
            b9, b10 = _f(rec[9]), _f(rec[10])
            fv["buy"] = (None if b9 is None and b10 is None
                         else max(b9 or 0.0, b10 or 0.0))
            out.append((d, fv))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="round-2 TV features -> tv_features_history")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    all_rows = []
    for fname, ticker in FULL_FILES.items():
        p = TV_DIR / fname
        if not p.exists():
            print(f"SKIP {fname}: not present")
            continue
        feats = load_features(p)
        dates = [d for d, _ in feats]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise SystemExit(f"{fname}: dates not strictly increasing")
        n_row = 0
        per_feat = {k: 0 for k in list(FEATURE_COLS) + ["buy"]}
        for d, fv in feats:
            ka = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)
            for k, v in fv.items():
                if v is None:
                    continue           # warm-up / hidden plot: skip, don't fill
                all_rows.append((ticker, d, ka, k, v))
                per_feat[k] += 1
                n_row += 1
        nan_note = {k: len(feats) - n for k, n in per_feat.items()
                    if len(feats) - n > 0}
        print(f"{fname} -> {ticker}: {len(feats)} bars "
              f"({dates[0]} .. {dates[-1]}), {n_row} feature rows"
              + (f"; NaN skipped per feature: {nan_note}" if nan_note else ""))

    print(f"\nTotal feature rows: {len(all_rows)}")
    if args.dry_run:
        print("[dry-run] no DB writes.")
        return 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(all_rows), 1000):
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
                all_rows[i:i + 1000], page_size=1000)
            conn.commit()
        cur.execute("""SELECT feature, count(*) FROM tv_features_history
                       WHERE feature = ANY(%s) GROUP BY feature ORDER BY feature""",
                    (list(FEATURE_COLS) + ["buy"],))
        print("tv_features_history now holds (round-2 features):")
        for f, n in cur.fetchall():
            print(f"  {f:<12} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
