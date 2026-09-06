"""tv_step0_round2.py — Step 0 for the round-2 (45-column full-indicator)
TradingView exports in data/tradingview/BATS_<T>_1D_full.csv.

READ-ONLY. Verifies before any ingest:
  1. Column-duplication quirks (Trend/Trade duplicated, Buy all zeros,
     52-week columns empty, SPY ranges NaN, AAAU Trend NaN pre-2022-10-28).
  2. Repaint: Range High/Low vs the FIRST-ROUND CSV (expect exact) and vs
     mfr_snapshots (loader tolerances).
  3. Characterizes Trend/Trade price levels (step lengths, position vs
     close/bull/bear, vs hedgeye_risk_ranges when present) and the
     Trade.2/Trend.2 small-scale series (duration-counter hypothesis).
  4. Hurst Exponent 64/256 vs the live feed's hurst / hurst_3mo (July->now).
  5. Value ranges + flag counts for every column that will become a feature.

Run:  py tv_step0_round2.py
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from tradingview_ingest import load_csv as load_r1  # noqa: E402

NY = ZoneInfo("America/New_York")
TV_DIR = REPO / "data" / "tradingview"

TICKERS = {  # bot ticker -> (full csv, round-1 csv)
    "SPY":  ("BATS_SPY_1D_full.csv",  "BATS_SPY_1D.csv"),
    "UUP":  ("BATS_UUP_1D_full.csv",  "BATS_UUP_1D.csv"),
    "USO":  ("BATS_USO_1D_full.csv",  "BATS_USO_1D.csv"),
    "AAAU": ("BATS_AAAU_1D_full.csv", "BATS_AAAU_1D.csv"),
    "TLT":  ("BATS_TLT_1D_full.csv",  "BATS_TLT_1D.csv"),
}

# positional map (duplicate header names force index-based parsing)
COLS = {
    "time": 0, "open": 1, "high": 2, "low": 3, "close": 4,
    "trend_lvl": 5, "trade_lvl": 6, "trend_dup": 7, "trade_dup": 8,
    "buy": 9, "buy_dup": 10, "mega_buy": 11, "sell": 12, "mega_sell": 13,
    "up_t1": 14, "down_t1": 15, "up_t2": 16, "down_t2": 17,
    "period_open": 18, "bull": 19, "bear": 20, "rh": 21, "rl": 22,
    "lth": 23, "ltl": 24, "volume": 25, "hurst64": 26, "hurst256": 27,
    "period1": 28, "period2": 29, "period3": 30, "vixfix": 31,
    "volatility": 40, "trade2": 41, "trend2": 42,
    "wk52_hi": 43, "wk52_lo": 44,
}


def _f(s):
    if s is None or s == "" or s.upper() == "NAN":
        return None
    return float(s)


def load_full(path: Path):
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rd = csv.reader(fh)
        header = next(rd)
        assert len(header) == 45, f"{path.name}: {len(header)} columns, expected 45"
        for rec in rd:
            r = {k: _f(rec[i]) for k, i in COLS.items() if k != "time"}
            r["date"] = datetime.fromtimestamp(int(rec[0]), tz=NY).date()
            rows.append(r)
    return rows


def quirks(t, rows):
    n = len(rows)
    print(f"\n### {t}: {n} rows, {rows[0]['date']} .. {rows[-1]['date']}")
    dup_trend = sum(1 for r in rows if r["trend_lvl"] != r["trend_dup"]
                    and not (r["trend_lvl"] is None and r["trend_dup"] is None))
    dup_trade = sum(1 for r in rows if r["trade_lvl"] != r["trade_dup"]
                    and not (r["trade_lvl"] is None and r["trade_dup"] is None))
    buy_nonzero = sum(1 for r in rows if r["buy"] not in (0.0, None)
                      or r["buy_dup"] not in (0.0, None))
    wk52 = sum(1 for r in rows if r["wk52_hi"] is not None or r["wk52_lo"] is not None)
    print(f"  dup-column mismatches: Trend {dup_trend}, Trade {dup_trade}; "
          f"Buy non-zero rows: {buy_nonzero}; 52wk non-empty: {wk52}")
    for col in ("trend_lvl", "trade_lvl", "rh", "rl", "lth", "ltl", "bull",
                "bear", "hurst64", "hurst256", "up_t1", "down_t1", "up_t2",
                "down_t2", "period_open", "volatility", "vixfix",
                "period1", "period2", "period3", "trade2", "trend2"):
        vals = [r[col] for r in rows if r[col] is not None]
        n_nan = n - len(vals)
        first = next((r["date"] for r in rows if r[col] is not None), None)
        if vals:
            print(f"    {col:<12} nan={n_nan:<5} first={first}  "
                  f"[{min(vals):.6g} .. {max(vals):.6g}]")
        else:
            print(f"    {col:<12} ALL NaN")
    for col in ("mega_buy", "sell", "mega_sell"):
        c = Counter(r[col] for r in rows)
        print(f"    {col:<12} value counts: {dict(c)}")


def repaint(t, rows, cur):
    r1_all, r1_body, _ = load_r1(TV_DIR / TICKERS[t][1])
    r1 = {r["date"]: r for r in r1_body}
    n_cmp = n_exact = 0
    worst = 0.0
    for r in rows:
        o = r1.get(r["date"])
        if not o or r["rh"] is None or r["rl"] is None:
            continue
        n_cmp += 1
        d = max(abs(r["rh"] - o["rh"]), abs(r["rl"] - o["rl"]))
        worst = max(worst, d)
        if d < 1e-6:
            n_exact += 1
    print(f"  vs round-1 CSV ranges: {n_cmp} shared dates, exact {n_exact}, "
          f"max abs diff {worst:.6g}")
    # vs live feed (informational; loader owns the gate)
    cur.execute("""SELECT snapshot_date, range_low, range_high FROM mfr_snapshots
                   WHERE ticker=%s AND range_low IS NOT NULL""", (t,))
    live = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
    n_ov = n_ok = 0
    for r in rows:
        L = live.get(r["date"])
        if not L or r["rl"] is None:
            continue
        n_ov += 1
        if abs(r["rl"] - L[0]) <= 5.0001e-4 and abs(r["rh"] - L[1]) <= 5.0001e-4:
            n_ok += 1
    print(f"  vs mfr_snapshots ranges: {n_ov} overlap, within rounding {n_ok}")


def characterize_levels(t, rows, cur):
    """What are Trend / Trade (price levels)?"""
    body = [r for r in rows if r["trend_lvl"] is not None and r["trade_lvl"] is not None]
    if not body:
        print("  levels: no rows with both Trend and Trade")
        return

    def runs(key):
        lens, cur_len = [], 1
        for a, b in zip(body, body[1:]):
            if b[key] == a[key]:
                cur_len += 1
            else:
                lens.append(cur_len)
                cur_len = 1
        lens.append(cur_len)
        lens.sort()
        return lens

    for key in ("trend_lvl", "trade_lvl"):
        lens = runs(key)
        above = sum(1 for r in body if r["close"] > r[key]) / len(body)
        print(f"  {key}: changes value every "
              f"~{sum(lens)/len(lens):.1f} bars (median run {lens[len(lens)//2]}, "
              f"max {lens[-1]}); close above it {above*100:.0f}% of bars")
    between = sum(1 for r in body if r["bear"] is not None
                  and min(r['trend_lvl'], r['trade_lvl'])
                  >= r["bear"] - 1e-9 and max(r['trend_lvl'], r['trade_lvl'])
                  <= r["bull"] + 1e-9) / len(body)
    trade_closer = sum(1 for r in body
                       if abs(r["close"] - r["trade_lvl"])
                       <= abs(r["close"] - r["trend_lvl"])) / len(body)
    print(f"  both levels inside [bear, bull] band: {between*100:.0f}% of bars; "
          f"Trade closer to close than Trend: {trade_closer*100:.0f}%")
    eq_bull = sum(1 for r in body if r["bull"] is not None
                  and abs(r['trend_lvl'] - r['bull']) < 1e-6) / len(body)
    eq_bear = sum(1 for r in body if r["bear"] is not None
                  and abs(r['trend_lvl'] - r['bear']) < 1e-6) / len(body)
    mid = sum(1 for r in body if r["bull"] is not None
              and abs((r["bull"] + r["bear"]) / 2 - r['trend_lvl']) < 1e-4) / len(body)
    print(f"  Trend == bull level {eq_bull*100:.0f}% / == bear {eq_bear*100:.0f}% "
          f"/ == midpoint(bull,bear) {mid*100:.0f}%")
    # Hedgeye risk-range comparison, if any levels exist for this ticker
    try:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name='hedgeye_risk_ranges'""")
        cols = [r[0] for r in cur.fetchall()]
        lvl_cols = [c for c in cols if "trend" in c or "trade" in c]
        cur.execute("SELECT count(*) FROM hedgeye_risk_ranges WHERE ticker=%s", (t,))
        n_rr = cur.fetchone()[0]
        print(f"  hedgeye_risk_ranges: {n_rr} rows for {t}; level-ish columns: {lvl_cols}")
    except Exception as e:
        print(f"  hedgeye_risk_ranges lookup failed: {e}")


def characterize_counters(t, rows):
    """Trade.2 / Trend.2 — duration-counter hypothesis."""
    for key, lvl in (("trade2", "trade_lvl"), ("trend2", "trend_lvl")):
        body = [r for r in rows if r[key] is not None]
        if not body:
            print(f"  {key}: all NaN")
            continue
        ints = sum(1 for r in body if float(r[key]).is_integer()) / len(body)
        inc1 = drops = 0
        reset_on_lvl_change = lvl_changes = 0
        for a, b in zip(body, body[1:]):
            d = b[key] - a[key]
            if d == 1:
                inc1 += 1
            elif d < 0:
                drops += 1
            if a[lvl] is not None and b[lvl] is not None and a[lvl] != b[lvl]:
                lvl_changes += 1
                if b[key] < a[key]:
                    reset_on_lvl_change += 1
        n = len(body) - 1
        print(f"  {key}: integer {ints*100:.0f}%, +1 steps {inc1}/{n} "
              f"({inc1/n*100:.0f}%), resets {drops}; resets coinciding with "
              f"{lvl} change: {reset_on_lvl_change}/{lvl_changes}")


def hurst_check(t, rows, cur):
    cur.execute("""SELECT snapshot_date, hurst, hurst_3mo FROM mfr_snapshots
                   WHERE ticker=%s AND hurst IS NOT NULL
                     AND snapshot_date >= '2026-07-01'""", (t,))
    live = {r[0]: (float(r[1]), float(r[2]) if r[2] is not None else None)
            for r in cur.fetchall()}
    d64, d256 = [], []
    for r in rows:
        L = live.get(r["date"])
        if not L:
            continue
        if r["hurst64"] is not None:
            d64.append(abs(r["hurst64"] - L[0]))
        if r["hurst256"] is not None and L[1] is not None:
            d256.append(abs(r["hurst256"] - L[1]))
    for name, ds in (("Hurst64 vs feed hurst", d64),
                     ("Hurst256 vs feed hurst_3mo", d256)):
        if ds:
            ds.sort()
            print(f"  {name}: n={len(ds)}, median {ds[len(ds)//2]:.4f}, "
                  f"max {ds[-1]:.4f}, within 0.01: {sum(x <= 0.01 for x in ds)}")
        else:
            print(f"  {name}: no overlap")


def main():
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for t, (full, _r1) in TICKERS.items():
            rows = load_full(TV_DIR / full)
            quirks(t, rows)
            repaint(t, rows, cur)
            characterize_levels(t, rows, cur)
            characterize_counters(t, rows)
            hurst_check(t, rows, cur)
    return 0


if __name__ == "__main__":
    sys.exit(main())
