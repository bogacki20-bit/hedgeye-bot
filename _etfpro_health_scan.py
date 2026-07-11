"""
_etfpro_health_scan.py — READ-ONLY bug-#2 health scan: "ETF Pro looks turned off
since the RR-staleness deploy (7/6)". Run on the Lenovo:
    python _etfpro_health_scan.py
Writes NOTHING. Prints:
  1. deploy state    — live sha / boot time / listener heartbeat age
  2. etfpro table    — latest weeks, row counts, bias split (07-06 populated?)
  3. polling universe— source breakdown incl. etf_pro_long/short + stale flags
  4. alerts_fired    — per-day counts, total vs ETF-Pro-member names, 14 days
  5. trade_recs      — scanner output per day, 14 days
  6. MFR fan-out     — snapshot rows per date, last 5 dates
  7. recent RTA rows — the position-close feed (input for same-day removal)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if not (os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")):
    try:
        for ln in open(os.path.join(os.path.dirname(__file__), ".env")):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass

import db_pg
from datetime import datetime, timezone

def q(sql, args=None):
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()

def hdr(s):
    print("\n" + "=" * 62 + f"\n{s}\n" + "=" * 62)

# 1 ─ deploy state
hdr("1. DEPLOY STATE")
for key in ("bot_git_sha", "bot_boot_at", "telegram_listener_heartbeat"):
    rows = q("SELECT value, updated_at FROM bot_state WHERE key=%s", (key,))
    if rows:
        val, upd = rows[0]
        age = ""
        if upd:
            secs = (datetime.now(timezone.utc) - upd).total_seconds()
            age = f"   (updated {secs/60:.1f} min ago)"
        print(f"  {key:<28} {str(val)[:60]}{age}")
    else:
        print(f"  {key:<28} MISSING")
print("  expect sha e512db2… (book fix) and heartbeat < 3 min")

# 2 ─ ETF Pro table
hdr("2. hedgeye_etf_pro_ranges — last 4 weeks")
for wk, n, nl, ns, nb in q("""
    SELECT week_of, count(*),
           count(*) FILTER (WHERE lower(bias)='long'),
           count(*) FILTER (WHERE lower(bias)='short'),
           count(*) FILTER (WHERE bias IS NULL)
    FROM hedgeye_etf_pro_ranges GROUP BY week_of ORDER BY week_of DESC LIMIT 4"""):
    print(f"  week_of {wk}  rows={n:<4} long={nl:<3} short={ns:<3} null-bias={nb}")

# 3 ─ polling universe breakdown
hdr("3. POLLING UNIVERSE (live resolver)")
try:
    from tools.active_slice import polling_universe, source_breakdown
    uni = polling_universe()
    print(f"  polling_universe: {len(uni)} tickers")
    for k, v in sorted(source_breakdown().items()):
        n = len(v) if hasattr(v, "__len__") else v
        flag = "  <-- STALE, excluded" if str(k).startswith("stale_") else ""
        print(f"    {k:<28} {n}{flag}")
except Exception as e:
    print(f"  resolver failed: {e}")

# 4 ─ alerts per day: total vs ETF-Pro members
hdr("4. alerts_fired per day — last 14 days (deploy was 7/6 ~9pm)")
rows = q("""
    WITH ep AS (SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges
                WHERE week_of = (SELECT max(week_of) FROM hedgeye_etf_pro_ranges))
    SELECT fired_at::date, count(*),
           count(*) FILTER (WHERE a.ticker IN (SELECT ticker FROM ep))
    FROM alerts_fired a
    WHERE fired_at >= now() - interval '14 days'
    GROUP BY 1 ORDER BY 1""")
print("  date         total  on-ETF-Pro-names")
for d, n, ne in rows:
    print(f"  {d}   {n:<6} {ne}")
if not rows:
    print("  NO ALERTS AT ALL in 14 days — bigger than ETF Pro.")

# 5 ─ scanner output per day
hdr("5. trade_recommendations per day — last 14 days")
for d, n in q("""SELECT created_at::date, count(*) FROM trade_recommendations
                 WHERE created_at >= now() - interval '14 days'
                 GROUP BY 1 ORDER BY 1"""):
    print(f"  {d}   {n}")

# 6 ─ MFR fan-out coverage
hdr("6. mfr_snapshots rows per snapshot_date — last 5 dates")
for d, n in q("""SELECT snapshot_date, count(*) FROM mfr_snapshots
                 GROUP BY 1 ORDER BY 1 DESC LIMIT 5"""):
    print(f"  {d}   {n}")

# 7 ─ recent RTA (position-close feed preview)
hdr("7. hedgeye_rta — last 10 rows (same-day-removal input)")
try:
    cols = [r[0] for r in q("""SELECT column_name FROM information_schema.columns
                               WHERE table_name='hedgeye_rta' ORDER BY ordinal_position""")]
    print("  columns: " + ", ".join(cols))
    for r in q("SELECT * FROM hedgeye_rta ORDER BY 1 DESC LIMIT 10"):
        print("  " + " | ".join(str(x)[:28] for x in r))
except Exception as e:
    print(f"  hedgeye_rta read failed: {e}")

print("\nScan complete — read-only, nothing written.")
