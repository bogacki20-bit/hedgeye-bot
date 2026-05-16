"""Event-driven delta alerts for Hedgeye product feeds.

Compares the two most recent snapshots of each product and fires a
Telegram alert + records an alerts_fired row for each material change:

  Portfolio Solutions (hedgeye_portfolio_solutions)
    - ticker ADDED / REMOVED between the two latest snapshot_dates
    - rank jump > 20 positions (snapshot-over-snapshot, or rank_change_1w)
  ETF Pro (hedgeye_etf_pro_ranges)
    - ticker ADDED / REMOVED from the weekly range list
      (ETF Pro has no rank column, so list presence is the delta — the
       analogue of the spec's "top 10" move)
  Signal Strength (hedgeye_signal_strength)
    - is_added_today / is_removed_today flags on the latest snapshot
      (SS has no rank column, so these native flags are the SS delta —
       the analogue of the spec's ">20 positions" jump)

alerts_fired has no `kind` column; `boundary` carries the discriminator
('ps_delta' | 'etf_pro_delta' | 'ss_delta'). Telegram uses
notifier.send_telegram (the bridge has no telegram_send entrypoint).

CLI:
    python -m tools.event_driven_alerts            # alert + persist
    python -m tools.event_driven_alerts --dry-run  # print only
"""

from __future__ import annotations

import sys
import logging
import datetime as dt

log = logging.getLogger(__name__)

_RANK_JUMP_THRESHOLD = 20


def _two_latest_dates(cur, table: str, date_col: str) -> tuple:
    cur.execute(
        f"SELECT DISTINCT {date_col} FROM {table} "
        f"ORDER BY {date_col} DESC LIMIT 2"
    )
    rows = [r[0] for r in cur.fetchall()]
    today = rows[0] if rows else None
    prev = rows[1] if len(rows) > 1 else None
    return today, prev


def _ps_deltas(cur) -> list[dict]:
    today, prev = _two_latest_dates(cur, "hedgeye_portfolio_solutions",
                                    "snapshot_date")
    if not today:
        return []
    cur.execute("SELECT ticker, rank, rank_change_1w FROM "
                "hedgeye_portfolio_solutions WHERE snapshot_date=%s", (today,))
    cur_rows = {t: (rk, rc) for t, rk, rc in cur.fetchall()}
    prev_ranks = {}
    if prev:
        cur.execute("SELECT ticker, rank FROM hedgeye_portfolio_solutions "
                    "WHERE snapshot_date=%s", (prev,))
        prev_ranks = {t: rk for t, rk in cur.fetchall()}

    out = []
    for t, (rk, rc) in cur_rows.items():
        if prev and t not in prev_ranks:
            out.append({"ticker": t, "kind": "ps_delta",
                        "msg": f"{t} ADDED to Portfolio Solutions (rank {rk})"})
            continue
        jump = None
        if t in prev_ranks and rk is not None and prev_ranks[t] is not None:
            jump = prev_ranks[t] - rk  # +ve = moved up the list
        elif rc is not None:
            jump = rc
        if jump is not None and abs(jump) > _RANK_JUMP_THRESHOLD:
            direction = "UP" if jump > 0 else "DOWN"
            out.append({"ticker": t, "kind": "ps_delta",
                        "msg": f"{t} Portfolio Solutions rank moved {direction} "
                               f"{abs(jump)} positions (now rank {rk})"})
    for t in (prev_ranks.keys() - cur_rows.keys()) if prev else []:
        out.append({"ticker": t, "kind": "ps_delta",
                    "msg": f"{t} REMOVED from Portfolio Solutions"})
    return out


def _etf_pro_deltas(cur) -> list[dict]:
    today, prev = _two_latest_dates(cur, "hedgeye_etf_pro_ranges", "week_of")
    if not today or not prev:
        return []
    cur.execute("SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges "
                "WHERE week_of=%s", (today,))
    cur_set = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges "
                "WHERE week_of=%s", (prev,))
    prev_set = {r[0] for r in cur.fetchall()}
    out = []
    for t in sorted(cur_set - prev_set):
        out.append({"ticker": t, "kind": "etf_pro_delta",
                    "msg": f"{t} ADDED to ETF Pro range list (week {today})"})
    for t in sorted(prev_set - cur_set):
        out.append({"ticker": t, "kind": "etf_pro_delta",
                    "msg": f"{t} REMOVED from ETF Pro range list (week {today})"})
    return out


def _ss_deltas(cur) -> list[dict]:
    today, _ = _two_latest_dates(cur, "hedgeye_signal_strength",
                                 "snapshot_date")
    if not today:
        return []
    cur.execute("SELECT ticker, is_added_today, is_removed_today FROM "
                "hedgeye_signal_strength WHERE snapshot_date=%s", (today,))
    out = []
    for t, added, removed in cur.fetchall():
        if added:
            out.append({"ticker": t, "kind": "ss_delta",
                        "msg": f"{t} ADDED to Signal Strength ({today})"})
        elif removed:
            out.append({"ticker": t, "kind": "ss_delta",
                        "msg": f"{t} REMOVED from Signal Strength ({today})"})
    return out


def run(dry_run: bool = False) -> int:
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr)
        return 2

    deltas: list[dict] = []
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            deltas += _ps_deltas(cur)
            deltas += _etf_pro_deltas(cur)
            deltas += _ss_deltas(cur)
    except Exception as e:
        print(f"ERROR: delta computation failed: {e}", file=sys.stderr)
        return 3

    print(f"Detected {len(deltas)} event delta(s).")
    for d in deltas:
        print(f"  [{d['kind']}] {d['msg']}")

    if dry_run:
        print("[dry-run] no Telegram, no alerts_fired writes.")
        return 0

    sent = 0
    for d in deltas:
        try:
            from notifier import send_telegram
            send_telegram(f"HEDGEYE {d['kind'].upper()}", d["msg"], priority=2)
        except Exception as e:
            log.warning("telegram send failed for %s: %s", d["ticker"], e)
        try:
            import db_pg
            db_pg.record_alert(
                ticker=d["ticker"],
                boundary=d["kind"],
                signal_date=dt.date.today(),
                recommendation_text=d["msg"],
                suggested_action="EVENT",
            )
        except Exception as e:
            log.warning("record_alert failed for %s: %s", d["ticker"], e)
        sent += 1
    print(f"Fired {sent} alert(s).")
    return 0


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.event_driven_alerts")
    p.add_argument("--dry-run", action="store_true",
                   help="detect + print only; no Telegram, no DB writes")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(_cli())
