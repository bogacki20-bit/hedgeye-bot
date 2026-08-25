"""_acceptance_live.py — DATA acceptance: live DB vs the newest fixtures.

NOT A TEST: data moving is information to read after an ingest, not a build
failure — a test here is exactly what let an authorized ingest block a code
merge on 2026-08-24. Run it after every PM or book ingest:

    python _acceptance_live.py

Prints the deltas between the live roster/book and the newest fixture of each
kind (fixtures/pm_roster_*.json, fixtures/book_snapshot_*.json). Exits 0
unless the DB itself is unreachable. When the data has legitimately moved,
capture fresh fixtures rather than editing numbers by hand.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _newest(pattern):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def main() -> int:
    try:
        import db_pg
        db_pg._load_dotenv_fallback()
        cm = db_pg.get_conn()
    except Exception as e:
        print(f"DB UNREACHABLE: {e}")
        return 1

    with cm as conn, conn.cursor() as cur:
        cur.execute("SELECT hedgeye_group, count(*) FROM ticker_tags "
                    "WHERE hedgeye_bucket_0629 IS NOT NULL GROUP BY 1")
        live_sectors = {g: int(n) for g, n in cur.fetchall()}
        cur.execute("SELECT max(snapshot_date) FROM book_positions")
        live_snap = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(sum(market_value), 0) FROM book_positions "
                    "WHERE snapshot_date = %s", (live_snap,))
        live_total = float(cur.fetchone()[0] or 0)

    rp = _newest(os.path.join("fixtures", "pm_roster_*.json"))
    if rp:
        with open(rp, encoding="utf-8") as f:
            fx = json.load(f)
        fx_total, live_roster = fx["total"], sum(live_sectors.values())
        tag = "OK" if fx_total == live_roster else "MOVED"
        print(f"PM roster:  fixture {fx_total} ({fx['as_of']}) -> "
              f"live {live_roster}   {tag}")
        diffs = []
        for s in sorted(set(fx["sectors"]) | set(live_sectors)):
            a, b = fx["sectors"].get(s, 0), live_sectors.get(s, 0)
            if a != b:
                diffs.append(f"{s} {a}->{b}")
        print("sectors:    " + ("no change" if not diffs else " · ".join(diffs)))
    else:
        print("PM roster:  no fixture found")

    bp = _newest(os.path.join("fixtures", "book_snapshot_*.json"))
    if bp:
        with open(bp, encoding="utf-8") as f:
            fx = json.load(f)
        fx_total = sum(fx["account_values"].values())
        tag = "OK" if abs(fx_total - live_total) < 0.01 else "MOVED"
        print(f"book:       fixture ${fx_total:,.2f} ({fx['as_of']}) -> "
              f"live ${live_total:,.2f} ({live_snap})   {tag}")
    else:
        print("book:       no fixture found")

    print("(deltas are information, not failures — capture new fixtures when "
          "the data legitimately moves)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
