"""tradingview_ingest.py — load a TradingView MFR "Export chart data" CSV
into tv_mfr_history (migration 085).

    py tradingview_ingest.py --file data/tradingview/BATS_SPY_1D.csv --ticker SPY
    py tradingview_ingest.py --all          # every mapped file in data/tradingview/
    py tradingview_ingest.py --all --dry-run

Doctrine: Python owns the arithmetic, the import never touches mfr_snapshots,
and validation is loud. Hard failures abort with the offending rows printed:
non-monotonic or duplicate dates, NaN indicator values after warm-up,
range_high <= range_low, lt_high <= lt_low, bull <= bear, ranges insane vs the
bar's close. Leading warm-up rows (NaN ranges/levels) are skipped, not filled.

Repaint cross-check (operator decisions, 2026-09-06): every bar that also has
a live mfr_snapshots range must match within tolerance —
    ETFs: |diff| <= 0.0005 absolute (the live feed serves 3-dp values)
    ^VIX: |diff| <= 0.25% relative (the feed's VIX series differs from TVC's)
Beyond tolerance is LOGGED (not failed) when the drift is small (<= 0.05% of
close for ETFs, <= 1.5% for ^VIX) or the date falls in the known June-2026
echo window (2026-06-11 stale-feed day + its trailing-window decay through
2026-07-10, Step 0 finding). Anything larger aborts.

trend_tag: rule R3 (validated 353/354 in Step 0) — the tag for bar D is the
prior bar's close against the prior bar's levels: +1 above bull, -1 below
bear, else 0. The first post-warm-up bar has no prior levels -> NULL.

known_at = bar_date 09:30 America/New_York (published the prior evening;
Step 0 confirmed via weekend-row and pre-open-fetch evidence).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402

NY = ZoneInfo("America/New_York")
TV_DIR = REPO / "data" / "tradingview"

# csv basename -> mfr_snapshots ticker (--all uses this; --file/--ticker for
# anything new, e.g. BATS_TLT_1D.csv -> TLT once the file lands)
FILE_TICKERS = {
    "BATS_SPY_1D.csv":  "SPY",
    "BATS_UUP_1D.csv":  "UUP",
    "TVC_VIX_1D.csv":   "^VIX",
    "BATS_USO_1D.csv":  "USO",
    "BATS_AAAU_1D.csv": "AAAU",
    "BATS_TLT_1D.csv":  "TLT",
}

INDICATOR_COLS = ("bull", "bear", "rh", "rl", "lth", "ltl")

# repaint tolerances / logged-anomaly windows (Step 0, operator decisions)
ETF_ABS_TOL = 5.0001e-4
VIX_REL_TOL = 0.0025
ETF_WARN_REL = 0.0005     # <= 0.05% of close: log, don't fail
VIX_WARN_REL = 0.015      # <= 1.5%: log, don't fail
# Dated windows where the FEED's own values are known-divergent — logged, not
# failed. "*" = the 2026-06-11 stale-feed day and its trailing-window decay
# (Step 0). Add per-ticker windows here only for drift that is bounded AND
# explained. NOT a home for TLT: its feed range diverges from the TV
# indicator on ~2/3 of ALL overlap dates including the newest (0.07-0.29%,
# 2026-09-06 finding — closes identical to raw, no Hedgeye override, payload
# == columns), so TLT ingest deliberately keeps FAILING until the vendor
# discrepancy is resolved or the operator waives it.
LOGGED_WINDOWS = {
    "*": [(date(2026, 6, 11), date(2026, 7, 10))],
}

# range-vs-close sanity: VIX closes detach from the range on spike days
SANITY = {"^VIX": (0.15, 3.0), "*": (0.5, 2.0)}


class IngestError(Exception):
    pass


def _f(s):
    if s is None or s == "" or str(s).upper() == "NAN":
        return None
    return float(s)


def load_csv(path: Path):
    """(all_rows, body_rows, n_warmup). Rows are dicts with date/open/high/
    low/close/bull/bear/rh/rl/lth/ltl/vol, oldest first. body = rows after
    the leading warm-up (any NaN among the indicator columns)."""
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rd = csv.DictReader(fh)
        for rec in rd:
            rows.append({
                "date": datetime.fromtimestamp(int(rec["time"]), tz=NY).date(),
                "open": _f(rec["open"]), "high": _f(rec["high"]),
                "low": _f(rec["low"]), "close": _f(rec["close"]),
                "bull": _f(rec["Bullish Trend"]), "bear": _f(rec["Bearish Trend"]),
                "rh": _f(rec["Range High"]), "rl": _f(rec["Range Low"]),
                "lth": _f(rec["LT Range High"]), "ltl": _f(rec["LT Range Low"]),
                "vol": _f(rec.get("Volume")) if "Volume" in rec else None,
            })
    n_warm = 0
    for r in rows:
        if any(r[k] is None for k in INDICATOR_COLS):
            n_warm += 1
        else:
            break
    return rows, rows[n_warm:], n_warm


def validate(ticker: str, rows: list[dict], body: list[dict]) -> None:
    problems = []
    dates = [r["date"] for r in rows]
    dups = [d for d, c in Counter(dates).items() if c > 1]
    if dups:
        problems.append(f"duplicate dates: {dups[:5]}")
    if dates != sorted(dates):
        problems.append("dates not monotonic")
    lo_f, hi_f = SANITY.get(ticker, SANITY["*"])
    for r in body:
        d = r["date"]
        if any(r[k] is None for k in INDICATOR_COLS):
            problems.append(f"{d}: NaN indicator value after warm-up")
            continue
        if not r["rh"] > r["rl"]:
            problems.append(f"{d}: range_high {r['rh']} <= range_low {r['rl']}")
        if not r["lth"] > r["ltl"]:
            problems.append(f"{d}: lt_high {r['lth']} <= lt_low {r['ltl']}")
        if not r["bull"] > r["bear"]:
            problems.append(f"{d}: bull {r['bull']} <= bear {r['bear']}")
        if r["close"] is None or not (lo_f * r["close"] < r["rl"]
                                      and r["rh"] < hi_f * r["close"]):
            problems.append(f"{d}: range {r['rl']}..{r['rh']} insane vs close {r['close']}")
    if problems:
        detail = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise IngestError(f"{ticker}: {len(problems)} validation problem(s):\n  {detail}{more}")


def derive_trend(body: list[dict]) -> list:
    """R3 trend tag per body row (None for the first, no prior levels)."""
    tags = [None]
    for i in range(1, len(body)):
        p = body[i - 1]
        c = p["close"]
        tags.append(1 if c > p["bull"] else (-1 if c < p["bear"] else 0))
    return tags


def repaint_check(cur, ticker: str, body: list[dict]) -> None:
    cur.execute(
        """SELECT snapshot_date, range_low, range_high FROM mfr_snapshots
            WHERE ticker=%s AND range_low IS NOT NULL AND range_high IS NOT NULL""",
        (ticker,))
    live = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
    is_vix = ticker == "^VIX"
    n_ok = 0
    warns, fails = [], []
    for r in body:
        L = live.get(r["date"])
        if L is None:
            continue
        dlo, dhi = abs(r["rl"] - L[0]), abs(r["rh"] - L[1])
        if is_vix:
            rel = max(dlo / L[0], dhi / L[1])
            ok, warn = rel <= VIX_REL_TOL, rel <= VIX_WARN_REL
        else:
            rel = max(dlo, dhi) / r["close"]
            ok, warn = max(dlo, dhi) <= ETF_ABS_TOL, rel <= ETF_WARN_REL
        windows = LOGGED_WINDOWS["*"] + LOGGED_WINDOWS.get(ticker, [])
        in_window = any(a <= r["date"] <= b for a, b in windows)
        if ok:
            n_ok += 1
        elif warn or in_window:
            warns.append((r["date"], r["rl"], r["rh"], L, rel))
        else:
            fails.append((r["date"], r["rl"], r["rh"], L, rel))
    print(f"  repaint check vs live feed: {n_ok + len(warns) + len(fails)} overlap "
          f"dates — within tolerance {n_ok}, logged {len(warns)}, FAILED {len(fails)}")
    for d, rl, rh, L, rel in warns:
        print(f"    LOGGED {d}: csv {rl:.4f}/{rh:.4f}  live {L[0]:.4f}/{L[1]:.4f}"
              f"  ({rel * 100:.3f}%)")
    if fails:
        detail = "\n  ".join(
            f"{d}: csv {rl:.4f}/{rh:.4f}  live {L[0]:.4f}/{L[1]:.4f} ({rel * 100:.3f}%)"
            for d, rl, rh, L, rel in fails[:20])
        raise IngestError(f"{ticker}: repaint drift beyond logged tolerance:\n  {detail}")


def ingest(path: Path, ticker: str, dry_run: bool) -> int:
    rows, body, n_warm = load_csv(path)
    print(f"\n{path.name} -> {ticker}: {len(rows)} rows "
          f"({rows[0]['date']} .. {rows[-1]['date']}), warm-up skipped {n_warm}, "
          f"ingestable {len(body)}")
    validate(ticker, rows, body)
    tags = derive_trend(body)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        repaint_check(cur, ticker, body)
        if dry_run:
            print(f"  [dry-run] would upsert {len(body)} rows "
                  f"(trend_tag NULL on {sum(1 for t in tags if t is None)})")
            return len(body)
        n = 0
        for r, tag in zip(body, tags):
            d = r["date"]
            known_at = datetime(d.year, d.month, d.day, 9, 30, tzinfo=NY)
            cur.execute(
                """
                INSERT INTO tv_mfr_history
                    (ticker, bar_date, known_at, range_high, range_low,
                     lt_range_high, lt_range_low, bull_level, bear_level,
                     trend_tag, close, source_file, ingested_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (ticker, bar_date) DO UPDATE SET
                    known_at = EXCLUDED.known_at,
                    range_high = EXCLUDED.range_high,
                    range_low = EXCLUDED.range_low,
                    lt_range_high = EXCLUDED.lt_range_high,
                    lt_range_low = EXCLUDED.lt_range_low,
                    bull_level = EXCLUDED.bull_level,
                    bear_level = EXCLUDED.bear_level,
                    trend_tag = EXCLUDED.trend_tag,
                    close = EXCLUDED.close,
                    source_file = EXCLUDED.source_file,
                    ingested_at = NOW()
                """,
                (ticker, d, known_at, r["rh"], r["rl"], r["lth"], r["ltl"],
                 r["bull"], r["bear"], tag, r["close"], path.name),
            )
            n += 1
        conn.commit()
        cur.execute("SELECT count(*), min(bar_date), max(bar_date) "
                    "FROM tv_mfr_history WHERE ticker=%s", (ticker,))
        cnt, dmin, dmax = cur.fetchone()
        print(f"  upserted {n}; table now holds {cnt} rows {dmin} .. {dmax}")
        return n


def main() -> int:
    ap = argparse.ArgumentParser(description="TradingView MFR CSV -> tv_mfr_history")
    ap.add_argument("--file", help="CSV path")
    ap.add_argument("--ticker", help="mfr_snapshots ticker (e.g. SPY, ^VIX)")
    ap.add_argument("--all", action="store_true",
                    help="ingest every mapped file present in data/tradingview/")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    jobs: list[tuple[Path, str]] = []
    if args.all:
        for fname, ticker in FILE_TICKERS.items():
            p = TV_DIR / fname
            if p.exists():
                jobs.append((p, ticker))
    elif args.file and args.ticker:
        jobs.append((Path(args.file), args.ticker))
    else:
        ap.error("either --all or both --file and --ticker")

    total = 0
    try:
        for path, ticker in jobs:
            total += ingest(path, ticker, args.dry_run)
    except IngestError as e:
        print(f"\nERROR: {e}")
        return 1
    print(f"\n{'DRY-RUN ' if args.dry_run else ''}done — {total} rows across "
          f"{len(jobs)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
