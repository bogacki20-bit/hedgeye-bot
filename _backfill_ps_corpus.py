"""Backfill PS narrative into corpus_documents for the last 90 days.

Surgical version (2026-05-28): pulls each PS email's raw body from
hedgeye_emails_raw and the already-parsed ranks/actions from
hedgeye_portfolio_solutions / _portfolio_actions, then writes one
corpus_documents row per PS publication. Avoids re-running the full
process_email pipeline (which re-does inventory + MFR fan-out work
already complete from the original parse).

Idempotent via per-row UPSERT logic in parser_portfolio_solutions
._write_ps_corpus_row. Run as many times as needed.
"""
from __future__ import annotations

import sys

import db_pg
from parser_portfolio_solutions import _write_ps_corpus_row


def main() -> int:
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.message_id, e.subject, e.received_at, e.html_body
                  FROM hedgeye_emails_raw e
                 WHERE e.subject ILIKE %s
                   AND e.received_at >= NOW() - interval '90 days'
                 ORDER BY e.received_at ASC
                """,
                ("%Portfolio Solutions%Daily ETF Re-Rank%",),
            )
            emails = cur.fetchall()
    print(f"Found {len(emails)} PS emails", flush=True)

    ok = 0
    for i, (mid, subject, received, html_body) in enumerate(emails, 1):
        # Pull the structured data we already parsed for this email so
        # the corpus row's metadata stays in sync with the typed tables.
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(snapshot_date) FROM hedgeye_portfolio_solutions "
                    "WHERE source_email_id = %s",
                    (mid,),
                )
                snap_row = cur.fetchone()
                snapshot_date = snap_row[0] if snap_row and snap_row[0] else received.date()
                cur.execute(
                    "SELECT rank, ticker FROM hedgeye_portfolio_solutions "
                    "WHERE source_email_id = %s ORDER BY rank",
                    (mid,),
                )
                ranks = [{"rank": r[0], "ticker": r[1]} for r in cur.fetchall()]
                cur.execute(
                    "SELECT ticker, action, bps, commentary_raw "
                    "FROM hedgeye_portfolio_actions WHERE source_email_id = %s",
                    (mid,),
                )
                actions = [{"ticker": r[0], "action": r[1], "bps": r[2],
                            "commentary": r[3]} for r in cur.fetchall()]
        parsed = {"ranks": ranks, "actions": actions}
        _write_ps_corpus_row(mid, snapshot_date, subject, html_body, parsed)
        ok += 1
        if i % 10 == 0 or i == len(emails):
            print(f"  [{i}/{len(emails)}] last: {snapshot_date} ranks={len(ranks)} actions={len(actions)}",
                  flush=True)

    print(f"\nWrote/refreshed {ok} corpus_documents rows", flush=True)
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), MIN(source_date), MAX(source_date) "
                "FROM corpus_documents WHERE source='portfolio_solutions'"
            )
            n, mn, mx = cur.fetchone()
            print(f"corpus_documents PS rows: {n}  date_range=[{mn} .. {mx}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
