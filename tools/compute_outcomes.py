"""Build outcomes_log (realized round-trip P&L) from actions_log.

Walks actions_log chronologically per (account_number,
normalized_symbol), FIFO-matches BUY -> SELL legs, and writes one
outcomes_log row per closed lot. Links the alerts_fired row that fired
for the ticker within 24h BEFORE the opening BUY (if any) so each
outcome carries its decision context for ML.

Idempotent: UNIQUE(action_id_open, action_id_close) + ON CONFLICT DO
NOTHING — safe to re-run / re-backfill.

CLI:
    python -m tools.compute_outcomes --backfill            # whole history
    python -m tools.compute_outcomes --backfill --dry-run  # analyze only
    python -m tools.compute_outcomes --since 2026-05-01     # window
"""

from __future__ import annotations

import sys
import logging
import datetime as dt
from collections import defaultdict, deque

log = logging.getLogger(__name__)

_BUY_HINTS = ("YOU BOUGHT", "BOUGHT", "BUY", "REINVEST")
_SELL_HINTS = ("YOU SOLD", "SOLD", "SELL")


def _side(action: str) -> str | None:
    a = (action or "").upper()
    if any(h in a for h in _SELL_HINTS):
        return "SELL"
    if any(h in a for h in _BUY_HINTS):
        return "BUY"
    return None


def _quad_now() -> str | None:
    try:
        from tools.doctrine import current_quarterly_quad
        return current_quarterly_quad()
    except Exception:
        return None


def _find_alert_id(cur, ticker: str, before: dt.date) -> int | None:
    if not ticker or before is None:
        return None
    try:
        cur.execute(
            """
            SELECT id FROM alerts_fired
             WHERE UPPER(ticker) = UPPER(%s)
               AND fired_at <= %s::timestamptz
               AND fired_at >= %s::timestamptz - INTERVAL '24 hours'
             ORDER BY fired_at DESC LIMIT 1
            """,
            (ticker, before + dt.timedelta(days=1), before + dt.timedelta(days=1)),
        )
        r = cur.fetchone()
        return r[0] if r else None
    except Exception as e:
        log.debug("alert link lookup failed for %s: %s", ticker, e)
        return None


def run(backfill: bool = False, since: str | None = None,
        dry_run: bool = False) -> int:
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr)
        return 2

    where = ""
    params: list = []
    if since and not backfill:
        where = "WHERE run_date >= %s"
        params.append(since)

    rows_out: list[dict] = []
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, account_number, normalized_symbol, raw_symbol,
                       action, qty, price, run_date, ingested_at
                  FROM actions_log
                  {where}
                 ORDER BY run_date ASC, ingested_at ASC, id ASC
                """,
                params,
            )
            actions = cur.fetchall()

            # FIFO open-lot queues keyed by (account, normalized ticker).
            books: dict[tuple, deque] = defaultdict(deque)
            for (aid, acct, nsym, rsym, action, qty, price,
                 run_date, ingested) in actions:
                side = _side(action)
                tkr = (nsym or rsym or "").upper()
                if side is None or not tkr or qty is None or price is None:
                    continue
                q = abs(float(qty))
                p = float(price)
                key = (acct, tkr)
                if side == "BUY":
                    books[key].append({
                        "id": aid, "qty": q, "price": p,
                        "date": run_date, "rsym": rsym, "nsym": nsym,
                    })
                    continue
                # SELL: consume oldest BUY lots FIFO
                remaining = q
                while remaining > 1e-9 and books[key]:
                    lot = books[key][0]
                    matched = min(lot["qty"], remaining)
                    entry = lot["price"]
                    pnl_d = round((p - entry) * matched, 2)
                    pnl_p = round((p / entry - 1.0) * 100.0, 4) if entry else None
                    hold_min = None
                    if lot["date"] and run_date:
                        hold_min = int((run_date - lot["date"]).total_seconds()
                                       // 60) if hasattr(run_date - lot["date"],
                                                         "total_seconds") else \
                                   int((run_date - lot["date"]).days) * 1440
                    rows_out.append({
                        "action_id_open": lot["id"],
                        "action_id_close": aid,
                        "ticker": lot["rsym"] or tkr,
                        "normalized_ticker": tkr,
                        "account_number": acct,
                        "entry_price": entry,
                        "exit_price": p,
                        "qty": round(matched, 6),
                        "holding_period_minutes": hold_min,
                        "pnl_dollars": pnl_d,
                        "pnl_pct": pnl_p,
                        "was_winner": pnl_d > 0,
                        "open_date": lot["date"],
                        "closed_at": run_date,
                    })
                    lot["qty"] -= matched
                    remaining -= matched
                    if lot["qty"] <= 1e-9:
                        books[key].popleft()

            print(f"FIFO-matched {len(rows_out)} closed round trip(s) "
                  f"from {len(actions)} action rows.")
            wins = sum(1 for r in rows_out if r["was_winner"])
            if rows_out:
                tot = sum(r["pnl_dollars"] for r in rows_out)
                print(f"  winners={wins}/{len(rows_out)} "
                      f"({wins/len(rows_out)*100:.0f}%)  net P&L=${tot:,.2f}")

            if dry_run:
                for r in rows_out[:10]:
                    print(f"  {r['normalized_ticker']} {r['qty']}@"
                          f"{r['entry_price']}->{r['exit_price']} "
                          f"= ${r['pnl_dollars']:+,.2f}")
                print("[dry-run] no outcomes_log writes.")
                return 0

            quad = _quad_now()
            inserted = 0
            for r in rows_out:
                alert_id = _find_alert_id(cur, r["normalized_ticker"],
                                          r["open_date"])
                try:
                    cur.execute(
                        """
                        INSERT INTO outcomes_log
                          (alert_id, action_id_open, action_id_close, ticker,
                           normalized_ticker, account_number, entry_price,
                           exit_price, qty, holding_period_minutes,
                           pnl_dollars, pnl_pct, was_winner,
                           quad_regime_at_open, quad_regime_at_close, closed_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (action_id_open, action_id_close)
                          DO NOTHING
                        """,
                        (alert_id, r["action_id_open"], r["action_id_close"],
                         r["ticker"], r["normalized_ticker"],
                         r["account_number"], r["entry_price"], r["exit_price"],
                         r["qty"], r["holding_period_minutes"],
                         r["pnl_dollars"], r["pnl_pct"], r["was_winner"],
                         quad, quad, r["closed_at"]),
                    )
                    inserted += cur.rowcount or 0
                except Exception as e:
                    log.warning("insert failed (open=%s close=%s): %s",
                                r["action_id_open"], r["action_id_close"], e)
            conn.commit()
            print(f"Inserted {inserted} new outcomes_log row(s) "
                  f"({len(rows_out) - inserted} already present).")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    return 0


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.compute_outcomes")
    p.add_argument("--backfill", action="store_true",
                   help="process the entire actions_log history")
    p.add_argument("--since", default=None,
                   help="only run_date >= YYYY-MM-DD (ignored with --backfill)")
    p.add_argument("--dry-run", action="store_true",
                   help="FIFO-match + report only; no outcomes_log writes")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(backfill=a.backfill, since=a.since, dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(_cli())
