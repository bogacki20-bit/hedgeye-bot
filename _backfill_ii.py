"""Backfill hedgeye_investing_ideas from the unclassified Top-21
Leaderboard emails (2026-05-13 onward — everything missed by the
classifier subject-pattern bug).

Idempotent; safe to re-run. Surgical — runs parser_investing_ideas
directly on each candidate email, doesn't touch the classifier path.
"""
import db_pg
import parser_investing_ideas as pii


with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT message_id, subject, received_at
              FROM hedgeye_emails_raw
             WHERE (subject ILIKE %s OR subject ILIKE %s OR subject ILIKE %s)
               AND received_at >= NOW() - interval '30 days'
               AND COALESCE(classified_as,'') <> 'investing_ideas'
             ORDER BY received_at ASC
            """,
            ("%Top Stock Picks%Leaderboard%",
             "%Top 21 Long Ideas%",
             "%Investing Ideas%"),
        )
        candidates = cur.fetchall()

print(f"Found {len(candidates)} unclassified Top-21 / II emails")
ok, err = 0, 0
for mid, subj, recv in candidates:
    try:
        r = pii.process_email(mid, dry_run=False, fan_out=False)
        if isinstance(r, dict) and r.get("error"):
            err += 1
            print(f"  ERR {recv.date()}: {r['error']} ({subj[:70]})")
        else:
            ok += 1
            print(f"  OK  {recv.date()}: {(subj or '')[:70]}")
    except Exception as e:
        err += 1
        print(f"  CRASH {recv.date()}: {e}")

print(f"\nDone. ok={ok}  err={err}")

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(snapshot_date), COUNT(DISTINCT ticker), COUNT(*) "
            "FROM hedgeye_investing_ideas"
        )
        mx, n_t, n_r = cur.fetchone()
        print(f"hedgeye_investing_ideas: latest={mx} distinct_tickers={n_t} rows={n_r}")
        cur.execute(
            "SELECT snapshot_date, ticker FROM hedgeye_investing_ideas "
            "WHERE ticker='CASY' ORDER BY snapshot_date DESC LIMIT 5"
        )
        rs = cur.fetchall()
        print(f"CASY rows: {rs}")
