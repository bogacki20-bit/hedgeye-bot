"""shadow_scorecard.py — SHADOW WATCH PHASE instrumentation.

Grades the live shadow engine so the 2026-08-08 review is decided on numbers
rather than impressions. Four blocks:

  1. FORWARD COVERAGE  next-day close inside the prior day's shadow band.
     The core grade. Target 88-92%; persistently >94% means bands are too wide
     and k_width should come down.
  2. EXTREME BEHAVIOUR  every shadow rp <=0.20 / >=0.80 print, tracked to its
     next-5-session return. Did lows bounce, did highs stall.
  3. VALIDATOR HEALTH   flag rate vs the 8.6% empirical floor, split
     range_break (signal) / stale_band (MFR decaying) / divergence.
  4. SHD-SOURCED NAMES  names currently running on shadow ranges, days on
     shadow, and their coverage measured separately - these are the names where
     shadow is load-bearing rather than advisory.

Everything is persisted to shadow_scorecard so the review is queryable.

Read-only with respect to the trading path: nothing here feeds gating, sizing
or alerts. Small-n is reported explicitly - at the start of the watch phase the
rates are not yet readable and the block says so rather than implying a grade.

CLI:  python -m tools.shadow_scorecard            # print
      python -m tools.shadow_scorecard --persist  # print + store
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date

log = logging.getLogger(__name__)

COVERAGE_TARGET = (0.88, 0.92)
COVERAGE_TOO_WIDE = 0.94
FLAG_FLOOR = 0.086          # empirical false-positive floor, see shadow_params.json
MIN_N_READABLE = 200        # below this, coverage rates are noise


def _classes() -> dict:
    from tools.shadow_ingest import fetch_classes
    try:
        return fetch_classes()
    except Exception as e:
        log.warning("scorecard: class map unavailable: %s", e)
        return {}


def forward_coverage() -> dict:
    """% of next-day closes landing inside the prior day's shadow band.

    Uses shadow_price on session D+1 as the realised close, so the measurement
    is self-contained (same price source that built the band) and needs no
    extra vendor fetch. Only status='ok' bands count - the uncalibrated classes
    are excluded from the headline and reported separately by class.
    """
    import db_pg
    # A later snapshot_date is NOT the same thing as a later trading session:
    # runs on Sat/Sun/Mon all carry Friday's close, so comparing consecutive rows
    # by date alone scores Friday's band against Friday's own close and returns a
    # meaningless 100%. `bars` is the bar count fed to compute_range, so requiring
    # it to INCREASE guarantees a genuinely new session arrived between the two.
    sql = """
        WITH b AS (
          SELECT ticker, snapshot_date, shadow_low, shadow_high, shadow_price, bars,
                 lead(shadow_price) OVER (PARTITION BY ticker ORDER BY snapshot_date) nxt,
                 lead(bars)         OVER (PARTITION BY ticker ORDER BY snapshot_date) nxt_bars,
                 lead(snapshot_date) OVER (PARTITION BY ticker ORDER BY snapshot_date) nxt_d
          FROM shadow_snapshots
          WHERE shadow_low IS NOT NULL AND shadow_high > shadow_low)
        SELECT ticker, snapshot_date, nxt_d,
               (nxt >= shadow_low AND nxt <= shadow_high) AS inside
        FROM b
        WHERE nxt IS NOT NULL AND nxt_bars IS NOT NULL AND bars IS NOT NULL
          AND nxt_bars > bars
    """
    rows = []
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    except Exception as e:
        log.warning("scorecard: forward coverage failed: %s", e)
        return {"n": 0, "pct": None, "by_class": {}, "verdict": "unavailable"}

    cls = _classes()
    tot = hit = 0
    by: dict = {}
    for tk, d, nd, inside in rows:
        k = cls.get(tk, "equity")
        agg = by.setdefault(k, {"n": 0, "hit": 0})
        agg["n"] += 1
        agg["hit"] += 1 if inside else 0
        if k in ("equity", "etf"):          # headline = calibrated classes only
            tot += 1
            hit += 1 if inside else 0
    pct = (hit / tot) if tot else None
    by_class = {k: {"n": v["n"],
                    "pct": round(v["hit"] / v["n"], 4) if v["n"] else None}
                for k, v in sorted(by.items())}

    if not tot or tot < MIN_N_READABLE:
        verdict = "insufficient_n"
    elif pct > COVERAGE_TOO_WIDE:
        verdict = "too_wide"
    elif pct < COVERAGE_TARGET[0]:
        verdict = "too_tight"
    else:
        verdict = "in_band"
    return {"n": tot, "pct": round(pct, 4) if pct is not None else None,
            "by_class": by_class, "verdict": verdict}


def extreme_behavior(fwd_sessions: int = 5) -> dict:
    """Every shadow rp <=0.20 / >=0.80 print, tracked to its next-5-session return.

    A LOW hit = rp<=0.20 followed by a positive forward return (it bounced).
    A HIGH hit = rp>=0.80 followed by a non-positive return (it stalled/faded).
    """
    import db_pg
    # Same session-vs-snapshot trap as forward_coverage: step forward over rows
    # that actually added bars, so "5 sessions" means 5 sessions and not 5 runs.
    sql = """
        WITH s AS (
          SELECT ticker, snapshot_date, shadow_rp, shadow_price, bars,
                 row_number() OVER (PARTITION BY ticker, bars ORDER BY snapshot_date) rn
          FROM shadow_snapshots
          WHERE status = 'ok' AND shadow_rp IS NOT NULL AND shadow_price > 0
            AND bars IS NOT NULL),
        d AS (SELECT * FROM s WHERE rn = 1),          -- one row per (ticker, session)
        b AS (
          SELECT ticker, snapshot_date, shadow_rp, shadow_price,
                 lead(shadow_price, %s) OVER (PARTITION BY ticker ORDER BY bars) fwd
          FROM d)
        SELECT ticker, snapshot_date, shadow_rp, shadow_price, fwd
        FROM b WHERE fwd IS NOT NULL AND (shadow_rp <= 0.20 OR shadow_rp >= 0.80)
    """
    lo, hi = [], []
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(sql, (fwd_sessions,))
            for tk, d, rp, px, fwd in cur.fetchall():
                ret = float(fwd) / float(px) - 1.0
                (lo if float(rp) <= 0.20 else hi).append((tk, str(d), float(rp), ret))
    except Exception as e:
        log.warning("scorecard: extremes failed: %s", e)

    def _agg(rows, is_low):
        if not rows:
            return {"n": 0, "hit_pct": None, "avg_fwd5": None}
        hits = sum(1 for _, _, _, r in rows if (r > 0 if is_low else r <= 0))
        return {"n": len(rows), "hit_pct": round(hits / len(rows), 4),
                "avg_fwd5": round(sum(r for *_, r in rows) / len(rows), 4)}
    return {"low": _agg(lo, True), "high": _agg(hi, False),
            "fwd_sessions": fwd_sessions}


def validator_health(days: int = 7) -> dict:
    """Flag rate vs the 8.6% floor, split range_break / stale_band / divergence."""
    import db_pg
    out = {"validated": 0, "flagged": 0, "flag_rate": None,
           "range_break": 0, "stale_band": 0, "divergence": 0, "days": days}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("""
              SELECT count(*), count(*) FILTER (WHERE flagged),
                     count(*) FILTER (WHERE 'range_break' = ANY(COALESCE(flags,'{}'))),
                     count(*) FILTER (WHERE 'stale_band'  = ANY(COALESCE(flags,'{}'))),
                     count(*) FILTER (WHERE 'rp_divergence' = ANY(COALESCE(flags,'{}'))
                                        OR 'width_divergence' = ANY(COALESCE(flags,'{}')))
              FROM shadow_validation
              WHERE snapshot_date > CURRENT_DATE - %s""", (days,))
            n, f, rb, sb, dv = cur.fetchone()
            out.update({"validated": n, "flagged": f, "range_break": rb,
                        "stale_band": sb, "divergence": dv,
                        "flag_rate": round(f / n, 4) if n else None})
    except Exception as e:
        log.warning("scorecard: validator health failed: %s", e)
    return out


def shd_sourced() -> dict:
    """Names currently on shadow ranges: days-on-shadow + their own coverage."""
    import db_pg
    from tools.shadow_ingest import shadow_failover_map
    try:
        fmap = shadow_failover_map()
    except Exception as e:
        log.warning("scorecard: failover map failed: %s", e)
        return {"n": 0, "names": [], "coverage_pct": None, "coverage_n": 0}
    if not fmap:
        return {"n": 0, "names": [], "coverage_pct": None, "coverage_n": 0}
    tickers = sorted(fmap)
    names, cov_hit, cov_n = [], 0, 0
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("""
              SELECT v.ticker,
                     (SELECT max(snapshot_date) FROM mfr_snapshots m
                       WHERE m.ticker = v.ticker AND m.range_low IS NOT NULL) AS last_mfr
              FROM (SELECT unnest(%s::text[]) AS ticker) v""", (tickers,))
            for tk, last in cur.fetchall():
                days = (date.today() - last).days if last else None
                names.append({"ticker": tk, "days_on_shadow": days,
                              "last_mfr": str(last) if last else "never"})
            cur.execute("""
              WITH b AS (
                SELECT ticker, shadow_low, shadow_high, bars,
                       lead(shadow_price) OVER (PARTITION BY ticker ORDER BY snapshot_date) nxt,
                       lead(bars)         OVER (PARTITION BY ticker ORDER BY snapshot_date) nxt_bars
                FROM shadow_snapshots
                WHERE ticker = ANY(%s) AND status='ok'
                  AND shadow_low IS NOT NULL AND shadow_high > shadow_low)
              SELECT count(*), count(*) FILTER (WHERE nxt >= shadow_low AND nxt <= shadow_high)
              FROM b WHERE nxt IS NOT NULL AND nxt_bars > bars""", (tickers,))
            cov_n, cov_hit = cur.fetchone()
    except Exception as e:
        log.warning("scorecard: shd names failed: %s", e)
    names.sort(key=lambda d: (d["days_on_shadow"] is not None, -(d["days_on_shadow"] or 0)))
    return {"n": len(tickers), "names": names,
            "coverage_n": cov_n or 0,
            "coverage_pct": round(cov_hit / cov_n, 4) if cov_n else None}


def build_scorecard() -> dict:
    return {"as_of": str(date.today()), "coverage": forward_coverage(),
            "extremes": extreme_behavior(), "validator": validator_health(),
            "shd": shd_sourced()}


def _pct(x, nd=1):
    return f"{100*x:.{nd}f}%" if x is not None else "—"


def format_scorecard(s: dict) -> str:
    """The SHADOW SCORECARD block. Pure."""
    cov, ex, va, shd = s["coverage"], s["extremes"], s["validator"], s["shd"]
    L = [f"═══ SHADOW SCORECARD — {s['as_of']} (review 2026-08-08) ═══"]

    L.append(f"1. FORWARD COVERAGE (target 88-92%, >94% = too wide)")
    L.append(f"   overall (equity+etf): {_pct(cov['pct'])}  n={cov['n']}  -> {cov['verdict']}")
    for k, v in (cov.get("by_class") or {}).items():
        L.append(f"     {k:<9} {_pct(v['pct']):>7}  n={v['n']}")
    if cov["verdict"] == "insufficient_n":
        L.append(f"   ⚠ n below {MIN_N_READABLE} — rate not yet readable, do not grade on this")
    elif cov["verdict"] == "too_wide":
        L.append("   ⚠ ACTION: coverage persistently >94% — recalibrate k_width DOWN")

    L.append(f"2. EXTREME BEHAVIOUR (fwd-{ex['fwd_sessions']} sessions after an rp print)")
    lo, hi = ex["low"], ex["high"]
    L.append(f"   rp<=0.20 bounced : {_pct(lo['hit_pct'])}  n={lo['n']}"
             f"   avg fwd {_pct(lo['avg_fwd5'], 2)}")
    L.append(f"   rp>=0.80 stalled : {_pct(hi['hit_pct'])}  n={hi['n']}"
             f"   avg fwd {_pct(hi['avg_fwd5'], 2)}")
    if (lo["n"] + hi["n"]) < 30:
        L.append("   ⚠ small n — early prints, not yet a hit-rate")

    L.append(f"3. VALIDATOR HEALTH (last {va['days']}d, floor ~{_pct(FLAG_FLOOR)})")
    L.append(f"   flag rate {_pct(va['flag_rate'])} ({va['flagged']}/{va['validated']})"
             f"   range_break {va['range_break']} (signal)"
             f" · stale_band {va['stale_band']} · divergence {va['divergence']}")
    if va["flag_rate"] is not None and va["flag_rate"] > 2 * FLAG_FLOOR:
        L.append("   ⚠ flag rate >2x floor — check whether MFR coverage is decaying")

    L.append(f"4. SHD-SOURCED (shadow load-bearing): {shd['n']} names"
             f"   own coverage {_pct(shd['coverage_pct'])} n={shd['coverage_n']}")
    for it in (shd.get("names") or [])[:12]:
        d = it["days_on_shadow"]
        L.append(f"     {it['ticker']:<8} {'never had MFR' if d is None else str(d)+'d since MFR':<18}"
                 f" last_mfr={it['last_mfr']}")
    if shd["n"] > 12:
        L.append(f"     … +{shd['n']-12} more")
    return "\n".join(L)


def persist(s: dict) -> bool:
    import db_pg
    cov, ex, va, shd = s["coverage"], s["extremes"], s["validator"], s["shd"]
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("""
              INSERT INTO shadow_scorecard (as_of, coverage_pct, coverage_n,
                coverage_by_class, coverage_verdict, low_n, low_hit_pct, low_avg_fwd5,
                high_n, high_hit_pct, high_avg_fwd5, validated_n, flagged_n, flag_rate,
                n_range_break, n_stale_band, n_divergence, shd_names_n,
                shd_coverage_pct, shd_coverage_n, payload)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (as_of) DO UPDATE SET
                computed_at=now(), coverage_pct=EXCLUDED.coverage_pct,
                coverage_n=EXCLUDED.coverage_n, coverage_by_class=EXCLUDED.coverage_by_class,
                coverage_verdict=EXCLUDED.coverage_verdict, low_n=EXCLUDED.low_n,
                low_hit_pct=EXCLUDED.low_hit_pct, low_avg_fwd5=EXCLUDED.low_avg_fwd5,
                high_n=EXCLUDED.high_n, high_hit_pct=EXCLUDED.high_hit_pct,
                high_avg_fwd5=EXCLUDED.high_avg_fwd5, validated_n=EXCLUDED.validated_n,
                flagged_n=EXCLUDED.flagged_n, flag_rate=EXCLUDED.flag_rate,
                n_range_break=EXCLUDED.n_range_break, n_stale_band=EXCLUDED.n_stale_band,
                n_divergence=EXCLUDED.n_divergence, shd_names_n=EXCLUDED.shd_names_n,
                shd_coverage_pct=EXCLUDED.shd_coverage_pct,
                shd_coverage_n=EXCLUDED.shd_coverage_n, payload=EXCLUDED.payload
            """, (s["as_of"], cov["pct"], cov["n"], json.dumps(cov["by_class"]),
                  cov["verdict"], ex["low"]["n"], ex["low"]["hit_pct"], ex["low"]["avg_fwd5"],
                  ex["high"]["n"], ex["high"]["hit_pct"], ex["high"]["avg_fwd5"],
                  va["validated"], va["flagged"], va["flag_rate"], va["range_break"],
                  va["stale_band"], va["divergence"], shd["n"], shd["coverage_pct"],
                  shd["coverage_n"], json.dumps(s, default=str)))
            c.commit()
        return True
    except Exception as e:
        log.warning("scorecard: persist failed: %s", e)
        return False


def handle_scorecard_command(text):
    """Telegram hook — owns SCORECARD / SHADOW SCORECARD. None to decline."""
    t = (text or "").strip().upper()
    if t not in ("SCORECARD", "SHADOW SCORECARD", "SHADOW"):
        return None
    try:
        s = build_scorecard()
        persist(s)
        return format_scorecard(s)
    except Exception as e:
        log.warning("SCORECARD failed: %s", e)
        return f"SCORECARD unavailable: {e}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--persist", action="store_true")
    a = ap.parse_args()
    sc = build_scorecard()
    print(format_scorecard(sc))
    if a.persist:
        print(f"\npersisted: {persist(sc)}")
