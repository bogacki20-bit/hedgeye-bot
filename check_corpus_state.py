"""Quick diagnostic — count corpus_documents rows by source, plus list recent Hedgeye email subjects to find ETF Pro candidates."""
import json
import db_pg

def main():
    out = {"corpus_by_source": [], "recent_hedgeye_subjects": [],
           "etf_pro_candidates": []}
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Corpus document counts by source (find MFR rows specifically)
            cur.execute(
                "SELECT source, COUNT(*), MAX(source_date) "
                "FROM corpus_documents GROUP BY source ORDER BY COUNT(*) DESC"
            )
            for r in cur.fetchall():
                out["corpus_by_source"].append(
                    {"source": r[0], "rows": int(r[1]), "latest_date": str(r[2])}
                )
            # 2. Most recent Hedgeye email subjects (last 14 days)
            # Probe columns first since email schema varies
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='hedgeye_emails_raw'"
            )
            cols = sorted([r[0] for r in cur.fetchall()])
            out["hedgeye_emails_columns"] = cols
            cur.execute(
                "SELECT subject, received_at, classified_as, sender FROM hedgeye_emails_raw "
                "WHERE received_at >= NOW() - INTERVAL '14 days' "
                "ORDER BY received_at DESC LIMIT 30"
            )
            for r in cur.fetchall():
                out["recent_hedgeye_subjects"].append(
                    {"subject": r[0], "received_at": str(r[1]),
                     "classified_as": r[2], "sender": r[3]}
                )
            # 3. Specifically look for ETF Pro candidates — by subject OR classified_as
            cur.execute(
                "SELECT message_id, subject, received_at, classified_as FROM hedgeye_emails_raw "
                "WHERE subject ILIKE '%etf pro%' OR subject ILIKE '%etfpro%' "
                "   OR classified_as = 'etf_pro' "
                "ORDER BY received_at DESC LIMIT 15"
            )
            for r in cur.fetchall():
                out["etf_pro_candidates"].append(
                    {"message_id": r[0], "subject": r[1],
                     "received_at": str(r[2]), "classified_as": r[3]}
                )
            # 4. Counts by classified_as for the last 30 days
            cur.execute(
                "SELECT classified_as, COUNT(*) FROM hedgeye_emails_raw "
                "WHERE received_at >= NOW() - INTERVAL '30 days' "
                "GROUP BY classified_as ORDER BY COUNT(*) DESC"
            )
            out["classified_counts_30d"] = [
                {"classified_as": r[0], "count": int(r[1])} for r in cur.fetchall()
            ]
    print(json.dumps(out, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
