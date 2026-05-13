"""Reconcile alerts_fired against actions_log.

Three buckets:
  ACTED       — alerts_fired ⨯ actions_log on normalized_symbol within
                ±window days. Signal got acted on.
  SKIPPED     — alerts_fired with no actions_log match in window.
  UNPROMPTED  — actions_log with no alerts_fired match in window.

CLI:
    python -m tools.reconcile_alerts_actions
    python -m tools.reconcile_alerts_actions --since 2026-05-08
    python -m tools.reconcile_alerts_actions --on 2026-05-08
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("reconcile_alerts_actions")


def reconcile(*, since: date | None = None, on: date | None = None,
              window_days: int = 1) -> dict:
    import db_pg

    # Filters: every query gets a date constraint that matches the requested
    # scope. We pass the same `since`/`on` to both sides.
    if on is not None:
        a_date_clause = "a.fired_at::date = %(d)s"
        x_date_clause = "x.run_date = %(d)s"
        params = {"d": on, "window": window_days}
        scope = f"on={on}"
    elif since is not None:
        a_date_clause = "a.fired_at::date >= %(d)s"
        x_date_clause = "x.run_date >= %(d)s"
        params = {"d": since, "window": window_days}
        scope = f"since={since}"
    else:
        a_date_clause = "TRUE"
        x_date_clause = "TRUE"
        params = {"window": window_days}
        scope = "all"

    sql_acted = f"""
        SELECT a.id AS alert_id, a.ticker, a.fired_at::date AS alert_date,
               a.suggested_action, a.suggested_dollars,
               x.id AS action_id, x.run_date AS action_date,
               x.account_name, x.action, x.raw_symbol,
               x.normalized_symbol, x.qty, x.price, x.amount
          FROM alerts_fired a
          JOIN actions_log x
            ON COALESCE(x.normalized_symbol, x.raw_symbol) = a.ticker
           AND x.run_date BETWEEN a.fired_at::date - (%(window)s || ' days')::interval
                              AND a.fired_at::date + (%(window)s || ' days')::interval
         WHERE {a_date_clause}
         ORDER BY a.fired_at DESC
    """

    sql_skipped = f"""
        SELECT a.id AS alert_id, a.ticker, a.fired_at::date AS alert_date,
               a.fired_at, a.suggested_action, a.suggested_dollars,
               a.boundary, a.range_zone
          FROM alerts_fired a
         WHERE {a_date_clause}
           AND NOT EXISTS (
               SELECT 1 FROM actions_log x
                WHERE COALESCE(x.normalized_symbol, x.raw_symbol) = a.ticker
                  AND x.run_date BETWEEN a.fired_at::date - (%(window)s || ' days')::interval
                                      AND a.fired_at::date + (%(window)s || ' days')::interval
           )
         ORDER BY a.fired_at DESC
    """

    sql_unprompted = f"""
        SELECT x.id AS action_id, x.run_date AS action_date,
               x.account_name, x.account_number, x.action,
               x.raw_symbol, x.normalized_symbol,
               x.qty, x.price, x.amount
          FROM actions_log x
         WHERE {x_date_clause}
           AND NOT EXISTS (
               SELECT 1 FROM alerts_fired a
                WHERE a.ticker = COALESCE(x.normalized_symbol, x.raw_symbol)
                  AND a.fired_at::date BETWEEN x.run_date - (%(window)s || ' days')::interval
                                            AND x.run_date + (%(window)s || ' days')::interval
           )
         ORDER BY x.run_date DESC, x.id DESC
    """

    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_acted, params)
            acted = _rows(cur)
            cur.execute(sql_skipped, params)
            skipped = _rows(cur)
            cur.execute(sql_unprompted, params)
            unprompted = _rows(cur)

    return {
        "window_days": window_days,
        "scope": scope,
        "counts": {"acted": len(acted), "skipped": len(skipped),
                   "unprompted": len(unprompted)},
        "acted": acted, "skipped": skipped, "unprompted": unprompted,
    }


def _rows(cur) -> list[dict]:
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--since", help="YYYY-MM-DD: include from this date forward")
    grp.add_argument("--on",    help="YYYY-MM-DD: limit to this single date")
    ap.add_argument("--window-days", type=int, default=1,
                    help="±N trading days alert↔action proximity (default 1)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    since = datetime.strptime(args.since, "%Y-%m-%d").date() if args.since else None
    on    = datetime.strptime(args.on,    "%Y-%m-%d").date() if args.on    else None

    result = reconcile(since=since, on=on, window_days=args.window_days)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
