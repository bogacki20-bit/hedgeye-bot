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
            cur.execute(
                "SELECT subject, from_addr, sent_at "
                "FROM hedgeye_emails_raw "
                "WHERE sent_at >= NOW() - INTERVAL '14 days' "
                "ORDER BY sent_at DESC LIMIT 30"
            )
            for r in cur.fetchall():
                out["recent_hedgeye_subjects"].append(
                    {"subject": r[0], "from": r[1], "sent_at": str(r[2])}
                )
            # 3. Specifically look for ETF Pro subject patterns
            cur.execute(
                "SELECT message_id, subject, sent_at FROM hedgeye_emails_raw "
                "WHERE subject ILIKE '%etf pro%' OR subject ILIKE '%etfpro%' "
                "ORDER BY sent_at DESC LIMIT 15"
            )
            for r in cur.fetchall():
                out["etf_pro_candidates"].append(
                    {"message_id": r[0], "subject": r[1], "sent_at": str(r[2])}
                )
    print(json.dumps(out, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
