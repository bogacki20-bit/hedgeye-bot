import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db_pg
with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute("SELECT DISTINCT ticker FROM mfr_snapshots "
                "WHERE ticker ILIKE '%%VIX%%' OR ticker ILIKE '%%VX%%' "
                "ORDER BY ticker")
    print("VIX-ish tickers:", [r[0] for r in cur.fetchall()])
    cur.execute("SELECT DISTINCT ticker FROM mfr_snapshots "
                "WHERE ticker IN ('VIX','^VIX','VIX.X','$VIX','CBOE:VIX','UVXY','VIXY','VXX')")
    print("exact candidates:", [r[0] for r in cur.fetchall()])
