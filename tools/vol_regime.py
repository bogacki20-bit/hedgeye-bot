"""
vol_regime.py — the vol-regime layer (build queue item 1).

Classifies the vol complex from mfr_snapshots — per index: trend (BULLISH =
vol-rising regime), position in its own risk range, range width, and the
5-trading-day width change (compressing = vol coming out / widening = vol
building). Writes one frozen fact row per index per date to vol_regime_daily
(migration 057). Everything else stamps vol regime by joining on date.

Python owns ALL classification; no LLM. Missing indices are reported loudly,
never silently skipped.

CLI:
    python -m tools.vol_regime --today             # classify + write today
    python -m tools.vol_regime --backfill          # every date with data
    python -m tools.vol_regime --backfill --dry-run
    python -m tools.vol_regime --show              # print current regime line
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("vol_regime")

# index -> sleeve. mfr_snapshots may carry these with or without the caret.
VOL_COMPLEX = {
    "VIX":  "equity",
    "VVIX": "vol-of-vol",   # enrolled 2026-07-11 — vol-of-vol regime axis
    "VXN":  "tech",
    "RVX":  "smallcap",
    "MOVE": "rates",
    "OVX":  "oil",
    "GVZ":  "gold",
}
_WIDTH_BAND = 0.10          # ±10% width change = steady

_MAP = {"trendBullish": "BULLISH", "trendBearish": "BEARISH",
        "trendNeutral": "NEUTRAL"}
_MOM = {"momentumBullish": "BULLISH", "momentumBearish": "BEARISH",
        "momentumNeutral": "NEUTRAL", "momentumNeutralDanger": "NEUTRAL"}


def classify_row(price, range_low, range_high, trend_signal, momentum_signal,
                 width_5d_ago=None) -> dict:
    """Pure per-index classification. width_5d_ago is width_pct 5 td earlier
    (None -> phase 'n-a')."""
    out = {"price": price, "trend": _MAP.get(trend_signal),
           "momentum": _MOM.get(momentum_signal),
           "range_low": range_low, "range_high": range_high,
           "range_pos": None, "width_pct": None, "width_chg5d": None,
           "phase": "n-a"}
    if price and range_low is not None and range_high is not None:
        rng = float(range_high) - float(range_low)
        if rng > 0:
            out["range_pos"] = round((float(price) - float(range_low)) / rng, 4)
        if float(price) > 0:
            out["width_pct"] = round(rng / float(price), 5)
    if out["width_pct"] is not None and width_5d_ago:
        chg = out["width_pct"] / float(width_5d_ago) - 1.0
        out["width_chg5d"] = round(chg, 4)
        out["phase"] = ("widening" if chg > _WIDTH_BAND
                        else "compressing" if chg < -_WIDTH_BAND
                        else "steady")
    return out


def _candidates() -> dict:
    """{index_name: [tickers to try, best first]}. '^'-prefixed spot symbols
    preferred; VX_F (VIX futures) is the historical fallback for VIX — spot
    ^VIX was only MFR-enrolled 2026-07-11, so past dates resolve to futures
    and new dates to spot, PER DATE (resolution happens in _snap)."""
    out = {name: [f"^{name}", name] for name in VOL_COMPLEX}
    out["VIX"].append("VX_F")
    return out


def _resolve_tickers(cur) -> dict:
    """{index_name: [tickers present anywhere in mfr_snapshots, best first]}.
    LOUD about indices with no data under any candidate."""
    cand = _candidates()
    flat = [t for v in cand.values() for t in v]
    cur.execute("SELECT DISTINCT ticker FROM mfr_snapshots WHERE ticker = ANY(%s)",
                (flat,))
    present = {r[0] for r in cur.fetchall()}
    out = {}
    for name, opts in cand.items():
        hits = [o for o in opts if o in present]
        if hits:
            out[name] = hits
        elif name not in _warned_missing:
            _warned_missing.add(name)
            log.warning("vol_regime: NO mfr data for %s (tried %s) — "
                        "warning once per run", name, opts)
    return out


_warned_missing: set = set()


def _snap(cur, ticker: str, d: date):
    cur.execute(
        """SELECT price, range_low, range_high, trend_signal, momentum_signal,
                  snapshot_date
           FROM mfr_snapshots
           WHERE ticker = %s AND snapshot_date <= %s
           ORDER BY snapshot_date DESC LIMIT 1""", (ticker, d))
    return cur.fetchone()


def _width_5d_ago(cur, ticker: str, d: date):
    cur.execute(
        """SELECT price, range_low, range_high FROM mfr_snapshots
           WHERE ticker = %s AND snapshot_date <= %s::date - 5
           ORDER BY snapshot_date DESC LIMIT 1""", (ticker, d))
    r = cur.fetchone()
    if not r or not r[0] or r[1] is None or r[2] is None or float(r[0]) <= 0:
        return None
    return (float(r[2]) - float(r[1])) / float(r[0])


def write_for_date(d: date, dry_run: bool = False) -> dict:
    """Classify every resolvable index for date d and upsert-once. Loud
    summary: which wrote, which had no data, which were stale (>3d old)."""
    import db_pg
    summary = {"date": str(d), "written": 0, "missing": [], "stale": [],
               "dry_run": dry_run}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        tickers = _resolve_tickers(cur)
        summary["missing"] = sorted(set(VOL_COMPLEX) - set(tickers))
        for name, opts in sorted(tickers.items()):
            # Per-date resolution: first candidate with a FRESH (<=3d) snapshot
            # at this date wins — spot ^VIX for new dates, VX_F for history.
            row, tkr = None, None
            for o in opts:
                r = _snap(cur, o, d)
                if r and (d - r[5]).days <= 3:
                    row, tkr = r, o
                    break
                if r and row is None:
                    row, tkr = r, o    # keep best stale as last resort marker
            if not row:
                summary["missing"].append(name)
                continue
            price, lo, hi, tr, mo, snap_d = row
            if (d - snap_d).days > 3:
                summary["stale"].append(f"{name}({snap_d})")
                continue
            c = classify_row(price, lo, hi, tr, mo,
                             width_5d_ago=_width_5d_ago(cur, tkr, d))
            if dry_run:
                summary["written"] += 1
                continue
            cur.execute(
                """INSERT INTO vol_regime_daily
                   (as_of, index_name, sleeve, price, trend, momentum,
                    range_pos, width_pct, width_chg5d, phase,
                    range_low, range_high)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (as_of, index_name) DO NOTHING""",
                (d, name, VOL_COMPLEX[name], c["price"], c["trend"],
                 c["momentum"], c["range_pos"], c["width_pct"],
                 c["width_chg5d"], c["phase"], c["range_low"], c["range_high"]))
            summary["written"] += cur.rowcount or 0
        if not dry_run:
            conn.commit()
    return summary


def regime_line(d: date | None = None) -> str:
    """One-line current regime for REPORT/alert headers, e.g.
    'VOL: VIX BEAR@0.31 compressing · MOVE BULL@0.72 widening · …'

    `d` is the AS-OF the caller is reporting on. Pass the resolved trading
    session, not the wall clock.

    2026-08-17: defaulting to date.today() made this block anchor on the BUILD
    DATE while every other block anchors on the data session. Two builds ten
    hours apart printed VVIX 87.48 [75.197-97.508] rp=0.5505 in both -- byte
    identical inputs -- and phase 'steady' then 'compressing', because
    vol_regime_daily had gained an as_of=2026-08-17 row whose phase is computed
    against a different ~3-session comparison bar. Same class as the rates
    anchor: a window resolved from the clock rather than from the data.
    The default remains today() for callers that genuinely mean "now", but the
    EOD pack passes its resolved session."""
    import db_pg
    d = d or date.today()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (index_name) index_name, trend, range_pos, phase,
                      price, range_low, range_high, as_of
               FROM vol_regime_daily WHERE as_of <= %s
               ORDER BY index_name, as_of DESC""", (d,))
        rows = cur.fetchall()
    if not rows:
        return "VOL: no regime data"
    short = {"BULLISH": "BULL", "BEARISH": "BEAR", "NEUTRAL": "NEUT", None: "?"}
    parts = []
    for name, tr, rp, ph, price, range_low, range_high, _as_of in sorted(rows):
        lvl = f"{float(price):.1f}" if price is not None else "?"
        rng = f" [{float(range_low):.1f}-{float(range_high):.1f}]" if (range_low is not None and range_high is not None) else ""
        rp_s = f" rp={float(rp):.2f}" if rp is not None else ""
        parts.append(f"{name} {lvl}{rng}{rp_s} {short.get(tr,'?')} {ph}")
    # State which regime rows were used AND what the anchor is. Without the
    # date, a phase that changes while price/range/rp stay identical is
    # unexplainable from the output. Without the anchor semantics, a reader
    # cannot tell whether these are live prints or session-close facts —
    # 2026-08-23: the report and the stat pack printed different VIX bands
    # for the same date and it took a write-path audit to establish that
    # NEITHER was live (both read this frozen daily table; one was anchored
    # on the wall clock). The label now says so explicitly in every output.
    used = sorted({str(r[7]) for r in rows if len(r) > 7 and r[7]})
    stamp = ((" (anchor: %s session close — stored daily rows, not live)"
              % ", ".join(used)) if used else "")
    return "VOL: " + " · ".join(parts) + stamp


def backfill(dry_run: bool = False) -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        tickers = _resolve_tickers(cur)
        if not tickers:
            print("LOUD FAIL: no vol-complex tickers in mfr_snapshots at all.")
            return {"dates": 0}
        cur.execute(
            "SELECT DISTINCT snapshot_date FROM mfr_snapshots "
            "WHERE ticker = ANY(%s) ORDER BY snapshot_date",
            (list(tickers.values()),))
        dates = [r[0] for r in cur.fetchall()]
    total = {"dates": 0, "rows": 0}
    for d in dates:
        s = write_for_date(d, dry_run=dry_run)
        total["dates"] += 1
        total["rows"] += s["written"]
    print(f"backfill: {total['dates']} dates, {total['rows']} index-rows"
          + (" [DRY-RUN]" if dry_run else ""))
    return total


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(prog="tools.vol_regime")
    ap.add_argument("--today", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.backfill:
        backfill(dry_run=a.dry_run)
    elif a.today:
        print(write_for_date(date.today(), dry_run=a.dry_run))
        print(regime_line())
    elif a.show:
        print(regime_line())
    else:
        ap.error("pick one: --today / --backfill / --show")
    sys.exit(0)
