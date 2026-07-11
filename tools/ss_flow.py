"""
ss_flow.py — Signal Strength roster add/drop events, structure-stamped at
event date (build queue item 3; migration 058).

ss_roster_history is already the event log (added_on / removed_on with
sources). This module stamps those transitions into ss_flow_events with the
same frozen-at-event-date semantics as ps_flow: structure from the event
date's own mfr_snapshots row, quad from quad_regime_history gated by
QUAD_CLEAN_START, vol regime joinable by date. Seed rows (the initial
baseline) are not events and are skipped — counted, never silent.

stamp_unstamped() is idempotent (scan history, insert what's missing), so the
same function serves live (called after apply_deltas / anchor reconcile) and
backfill:
    python -m tools.ss_flow --backfill
    python -m tools.ss_flow --backfill --dry-run
"""
from __future__ import annotations

import logging

# Shared stamp helpers + the operator's quad-clean gate — one doctrine, one code path.
from tools.ps_flow import _quad_for, _structure_for  # noqa: F401

log = logging.getLogger("ss_flow")


def stamp_unstamped(dry_run: bool = False) -> dict:
    """Insert stamped rows for every ss_roster_history transition not yet in
    ss_flow_events. Loud summary: events written, seeds skipped, NULL stamps."""
    import db_pg
    summary = {"adds": 0, "drops": 0, "seed_skipped": 0, "null_structure": 0,
               "dry_run": dry_run}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ss_roster_history WHERE add_source='seed'")
        summary["seed_skipped"] = cur.fetchone()[0]

        # Unstamped ADDS (non-seed) …
        cur.execute("""
            SELECT h.ticker, h.added_on, h.add_source FROM ss_roster_history h
            WHERE h.add_source <> 'seed'
              AND NOT EXISTS (SELECT 1 FROM ss_flow_events e
                              WHERE e.ticker = h.ticker AND e.event = 'add'
                                AND e.event_date = h.added_on)
            ORDER BY h.added_on, h.ticker""")
        adds = cur.fetchall()
        # … and unstamped DROPS (a seed row's REMOVAL is still a real event).
        cur.execute("""
            SELECT h.ticker, h.removed_on, h.remove_source FROM ss_roster_history h
            WHERE h.removed_on IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM ss_flow_events e
                              WHERE e.ticker = h.ticker AND e.event = 'drop'
                                AND e.event_date = h.removed_on)
            ORDER BY h.removed_on, h.ticker""")
        drops = cur.fetchall()

        quad_cache: dict = {}
        for ev, rows, key in (("add", adds, "adds"), ("drop", drops, "drops")):
            for ticker, d, src in rows:
                if d not in quad_cache:
                    quad_cache[d] = _quad_for(cur, d)
                mq, qq = quad_cache[d]
                st = _structure_for(cur, ticker, d)
                if st["price"] is None:
                    summary["null_structure"] += 1
                if dry_run:
                    summary[key] += 1
                    continue
                cur.execute(
                    """INSERT INTO ss_flow_events
                       (event_date, ticker, event, src, quad_monthly,
                        quad_quarterly, price, range_pos, trend_signal,
                        momentum_signal, hurst, iv, rv, ivpd, lt_pos)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (event_date, ticker, event) DO NOTHING""",
                    (d, ticker, ev, src, mq, qq, st["price"], st["range_pos"],
                     st["trend_signal"], st["momentum_signal"], st["hurst"],
                     st["iv"], st["rv"], st["ivpd"], st["lt_pos"]))
                summary[key] += cur.rowcount or 0
        if not dry_run:
            conn.commit()
    return summary


def churn_summary(days: int = 5) -> str:
    """Rolling churn line for REPORT: adds/drops over the window + sector
    tilt of adds (needs ticker_tags; untagged counted DARK, never dropped)."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT e.event, e.ticker, t.gics_sector
            FROM ss_flow_events e
            LEFT JOIN ticker_tags t ON t.ticker = e.ticker
            WHERE e.event_date >= CURRENT_DATE - %s::int
            ORDER BY e.event_date""", (days,))
        rows = cur.fetchall()
    if not rows:
        return f"SS FLOW {days}d: no roster changes"
    adds = [(t, s) for ev, t, s in rows if ev == "add"]
    drops = [(t, s) for ev, t, s in rows if ev == "drop"]
    by_sector: dict = {}
    dark = 0
    for _, s in adds:
        if s:
            by_sector[s] = by_sector.get(s, 0) + 1
        else:
            dark += 1
    tilt = ", ".join(f"{s} x{n}" for s, n in
                     sorted(by_sector.items(), key=lambda kv: -kv[1])[:4])
    parts = [f"SS FLOW {days}d: +{len(adds)}/-{len(drops)}"]
    if tilt:
        parts.append(f"adds tilt: {tilt}")
    if dark:
        parts.append(f"{dark} adds untagged (DARK)")
    parts.append("adds: " + " ".join(t for t, _ in adds[-10:]) if adds else "")
    parts.append("drops: " + " ".join(t for t, _ in drops[-10:]) if drops else "")
    return " · ".join(p for p in parts if p)


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(prog="tools.ss_flow")
    ap.add_argument("--backfill", action="store_true",
                    help="stamp every historical transition (idempotent)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--churn", action="store_true", help="print 5d churn line")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.churn:
        print(churn_summary())
    elif a.backfill:
        print(stamp_unstamped(dry_run=a.dry_run))
    else:
        ap.error("pick one: --backfill / --churn")
    sys.exit(0)
