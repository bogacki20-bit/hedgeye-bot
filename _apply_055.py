"""One-shot: apply migrations/055_rta_closes.sql (idempotent)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg

sql = open(os.path.join(os.path.dirname(__file__),
                        "migrations", "055_rta_closes.sql")).read()
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
    cur.execute("SELECT count(*) FROM rta_position_closes")
    print("rta_position_closes ready, rows:", cur.fetchone()[0])
