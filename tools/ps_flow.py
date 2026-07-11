"""
ps_flow.py — Portfolio Solutions add/drop events, structure-stamped at event
time (trade-like-Keith corpus; migration 056).

Adds/drops are the diff between consecutive hedgeye_portfolio_solutions
snapshot_dates. Stamps are taken from the EVENT DATE's own mfr_snapshots row
(nearest at-or-before, capped 3 days back) and quad_regime_history — never
from "now" — so live writes and backfill produce identical semantics.
Missing history stays NULL and is counted loudly.

Called by parser_portfolio_solutions.process_email after each rank upsert.
Backfill CLI:
    python -m tools.ps_flow --backfill            # diff every snapshot pair
    python -m tools.ps_flow --backfill --dry-run
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("ps_flow")


# ─────────────────────────── pure ───────────────────────────

def compute_deltas(prev: set, curr: set) -> tuple[set, set]:
    """(adds, drops) between consecutive roster sets. Pure."""
    prev, curr = set(prev or ()), set(curr or ())
    return curr - prev, prev - curr


# ─────────────────────────── stamps ───────────────────────────

# Operator ruling (roadmap 7/6 + confirmed 2026-07-11): historical quad values
# before the manual-confirm regime were unreliable — clean quad labeling starts
# 2026-07-06. Events before that date get NULL quad stamps (their STRUCTURE
# stamps remain valid — MFR data is real for any date). Never backfill a lie.
QUAD_CLEAN_START = date(2026, 7, 6)


def _quad_for(cur, d: date):
    if d < QUAD_CLEAN_START:
        return (None, None)
    try:
        cur.execute(
            """SELECT monthly_quad, quarterly_quad FROM quad_regime_history
               WHERE effective_at <= %s::timestamp + interval '23 hours 59 minutes'
               ORDER BY effective_at DESC LIMIT 1""", (d,))
        r = cur.fetchone()
        return (r[0], r[1]) if r else (None, None)
    except Exception as e:
        log.warning("quad stamp lookup failed for %s: %s", d, e)
        return (None, None)


def _structure_for(cur, ticker: str, d: date) -> dict:
    """mfr_snapshots stamp at-or-before d (max 3 days back — beyond that the
    stamp is a lie, so NULL)."""
    out = {k: None for k in ("price", "range_pos", "trend_signal",
                             "momentum_signal", "hurst", "iv", "rv", "ivpd",
                             "lt_pos")}
    try:
        cur.execute(
            """SELECT price,
                      (price - range_low) / NULLIF(range_high - range_low, 0),
                      trend_signal, momentum_signal, hurst, iv, rv,
                      (full_payload->>'ivpd')::numeric,
                      price - (full_payload->'ltRangeData'->>'upperRange')::numeric,
                      price - (full_payload->'ltRangeData'->>'lowerRange')::numeric
               FROM mfr_snapshots
               WHERE ticker = %s AND snapshot_date <= %s
                 AND snapshot_date >= %s::date - 3
               ORDER BY snapshot_date DESC LIMIT 1""", (ticker, d, d))
        r = cur.fetchone()
        if not r:
            return out
        (out["price"], out["range_pos"], out["trend_signal"],
         out["momentum_signal"], out["hurst"], out["iv"], out["rv"],
         out["ivpd"], above_hi, above_lo) = r
        if above_hi is not None and above_lo is not None:
            out["lt_pos"] = ("above" if above_hi > 0
                             else "below" if above_lo < 0 else "in")
    except Exception as e:
        log.warning("structure stamp failed for %s@%s: %s", ticker, d, e)
    return out


# ─────────────────────────── write ───────────────────────────

def write_events(event_date: date, adds: set, drops: set,
                 ranks: dict | None = None, dry_run: bool = False) -> dict:
    """Insert stamped add/drop rows. Idempotent (UNIQUE + DO NOTHING).
    Returns loud summary incl. how many stamps came back NULL."""
    import db_pg
    summary = {"date": str(event_date), "adds": len(adds), "drops": len(drops),
               "inserted": 0, "null_structure": 0, "dry_run": dry_run}
    if not adds and not drops:
        return summary
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        mq, qq = _quad_for(cur, event_date)
        summary["quad"] = f"{mq}/{qq}"
        for ev, names in (("add", adds), ("drop", drops)):
            for t in sorted(names):
                st = _structure_for(cur, t, event_date)
                if st["price"] is None:
                    summary["null_structure"] += 1
                if dry_run:
                    continue
                cur.execute(
                    """INSERT INTO ps_flow_events
                       (event_date, ticker, event, rank, quad_monthly,
                        quad_quarterly, price, range_pos, trend_signal,
                        momentum_signal, hurst, iv, rv, ivpd, lt_pos)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (event_date, ticker, event) DO NOTHING""",
                    (event_date, t, ev, (ranks or {}).get(t), mq, qq,
                     st["price"], st["range_pos"], st["trend_signal"],
                     st["momentum_signal"], st["hurst"], st["iv"], st["rv"],
                     st["ivpd"], st["lt_pos"]))
                summary["inserted"] += cur.rowcount or 0
        if not dry_run:
            conn.commit()
    return summary


def process_snapshot(snapshot_date: date, dry_run: bool = False) -> dict:
    """Diff snapshot_date's roster against the previous snapshot and write
    stamped events. Called by the PS parser after upsert_ranks."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, rank FROM hedgeye_portfolio_solutions "
                    "WHERE snapshot_date = %s", (snapshot_date,))
        curr = {t: rk for t, rk in cur.fetchall()}
        cur.execute("SELECT max(snapshot_date) FROM hedgeye_portfolio_solutions "
                    "WHERE snapshot_date < %s", (snapshot_date,))
        prev_date = (cur.fetchone() or [None])[0]
        if prev_date is None:
            return {"date": str(snapshot_date), "adds": 0, "drops": 0,
                    "note": "first snapshot — no baseline, no events"}
        cur.execute("SELECT ticker FROM hedgeye_portfolio_solutions "
                    "WHERE snapshot_date = %s", (prev_date,))
        prev = {r[0] for r in cur.fetchall()}
    adds, drops = compute_deltas(prev, set(curr))
    out = write_events(snapshot_date, adds, drops, ranks=curr, dry_run=dry_run)
    out["baseline"] = str(prev_date)
    return out


def backfill(dry_run: bool = False) -> dict:
    """Walk every consecutive snapshot pair chronologically."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT snapshot_date FROM "
                    "hedgeye_portfolio_solutions ORDER BY snapshot_date")
        dates = [r[0] for r in cur.fetchall()]
    total = {"pairs": 0, "adds": 0, "drops": 0, "inserted": 0,
             "null_structure": 0}
    for d in dates[1:]:
        s = process_snapshot(d, dry_run=dry_run)
        total["pairs"] += 1
        for k in ("adds", "drops", "inserted", "null_structure"):
            total[k] += s.get(k, 0)
        if s.get("adds") or s.get("drops"):
            print(f"  {d}: +{s.get('adds',0)}/-{s.get('drops',0)} "
                  f"(inserted {s.get('inserted',0)}, "
                  f"null-stamp {s.get('null_structure',0)}, quad {s.get('quad')})")
    print(f"backfill: {total['pairs']} snapshot pairs, "
          f"{total['adds']} adds / {total['drops']} drops, "
          f"{total['inserted']} rows written, "
          f"{total['null_structure']} with NULL structure stamp"
          + (" [DRY-RUN]" if dry_run else ""))
    return total


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(prog="tools.ps_flow")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.backfill:
        backfill(dry_run=a.dry_run)
    else:
        ap.error("only --backfill mode is available from the CLI")
    sys.exit(0)
