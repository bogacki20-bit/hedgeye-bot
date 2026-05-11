"""Tiny — just corpus_by_source counts."""
import json
import db_pg

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source, COUNT(*), MAX(source_date) "
            "FROM corpus_documents GROUP BY source ORDER BY COUNT(*) DESC"
        )
        rows = [{"source": r[0], "rows": int(r[1]), "latest_date": str(r[2])}
                for r in cur.fetchall()]
print(json.dumps(rows, indent=2, default=str), flush=True)
