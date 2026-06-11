"""Diagnose the CASY false-COVER alert.

Pulls every CASY-relevant row from corpus, typed product tables, MFR,
Quad map, and the active source-flags resolver — then runs the live
gate end-to-end to surface where the COVER verb came from.
"""
import db_pg
import importlib
import tools.active_slice as al
import decision_engine as de
from price_monitor import compute_zone
import yaml
import os

os.environ.setdefault("CURRENT_MONTHLY_QUAD_OVERRIDE",   "Quad 2")
os.environ.setdefault("CURRENT_QUARTERLY_QUAD_OVERRIDE", "Quad 3")
importlib.reload(al); importlib.reload(de); al.invalidate_cache()


def _norm(s):
    if not s: return "neutral"
    s = str(s).lower()
    if "bullish" in s: return "bullish"
    if "bearish" in s: return "bearish"
    return "neutral"


with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        print("=" * 78)
        print("1) Investing Ideas — was CASY EVER on the II list (long or short)?")
        print("=" * 78)
        cur.execute(
            """
            SELECT snapshot_date, rank, ticker, analyst, pct_change,
                   date_added, date_removed
              FROM hedgeye_investing_ideas
             WHERE ticker = 'CASY'
             ORDER BY snapshot_date DESC LIMIT 20
            """
        )
        rows = cur.fetchall()
        if not rows:
            print("  (zero rows — CASY has NEVER been in hedgeye_investing_ideas)")
        for r in rows: print(f"  {r}")

        print()
        print("=" * 78)
        print("2) II latest snapshot — what's the most-recent II publication date?")
        print("=" * 78)
        cur.execute(
            """
            SELECT MAX(snapshot_date), CURRENT_DATE - MAX(snapshot_date) AS age_days,
                   COUNT(DISTINCT ticker)
              FROM hedgeye_investing_ideas
            """
        )
        r = cur.fetchone()
        print(f"  most_recent_II_date: {r[0]}  age={r[1]} days  tickers_in_latest=?")
        cur.execute(
            "SELECT COUNT(DISTINCT ticker) FROM hedgeye_investing_ideas "
            "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM hedgeye_investing_ideas)"
        )
        print(f"  tickers in latest snapshot: {cur.fetchone()[0]}")

        print()
        print("=" * 78)
        print("3) Every other Hedgeye product table — CASY?")
        print("=" * 78)
        for tbl, dc in (("hedgeye_risk_ranges", "signal_date"),
                        ("hedgeye_signal_strength", "snapshot_date"),
                        ("hedgeye_portfolio_solutions", "snapshot_date"),
                        ("hedgeye_etf_pro_ranges", "week_of")):
            cur.execute(
                f"SELECT {dc}, COUNT(*) FROM {tbl} WHERE ticker='CASY' "
                f"GROUP BY {dc} ORDER BY {dc} DESC LIMIT 3"
            )
            rs = cur.fetchall()
            print(f"  {tbl}: " + (", ".join(f"{r[0]}({r[1]})" for r in rs) if rs else "(none)"))

        print()
        print("=" * 78)
        print("4) corpus_documents — CASY mentions")
        print("=" * 78)
        cur.execute(
            """
            SELECT source, source_date, metadata->'side' AS side,
                   metadata->'tickers' AS tickers,
                   LEFT(COALESCE(full_text,''), 220) AS snippet
              FROM corpus_documents
             WHERE (full_text ILIKE '%CASY%' OR metadata::text ILIKE '%CASY%')
               AND source IN ('investing_ideas','hedgeye_investing_ideas',
                              'signal_strength','portfolio_solutions','etf_pro')
             ORDER BY source_date DESC LIMIT 10
            """
        )
        rs = cur.fetchall()
        if not rs:
            print("  (no Hedgeye-product corpus rows mention CASY)")
        for src, dt, side, tickers, snip in rs:
            print(f"  source={src} date={dt}")
            print(f"    metadata.side={side} tickers={tickers}")
            print(f"    snippet: {(snip or '').replace(chr(10),' ')[:220]}")

print()
print("=" * 78)
print("5) Quad map for CASY")
print("=" * 78)
with open(r"C:\Projects\hedgeye-bot\config\mfr_quad_map.yaml", encoding="utf-8") as fh:
    qm = yaml.safe_load(fh)
print(f"  {qm.get('tickers', {}).get('CASY')}")

print()
print("=" * 78)
print("6) source_flags_for('CASY') and _ticker_side")
print("=" * 78)
flags = al.source_flags_for("CASY")
side  = de._ticker_side(flags)
print(f"  source_flags_for('CASY') = {flags}")
print(f"  _ticker_side()           = {side}")

print()
print("=" * 78)
print("7) source_breakdown — which buckets CONTAIN 'CASY'?")
print("=" * 78)
bd = al.source_breakdown()
for k, lst in bd.items():
    if "CASY" in lst:
        included = k in al._POLLING_INCLUDED_KEYS
        print(f"  bucket={k:30} in_polling_universe={included}")

print()
print("=" * 78)
print("8) Live gate — what action does the bot produce for CASY today?")
print("=" * 78)
with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT price, range_low, range_high, trend_signal, momentum_signal "
            "FROM mfr_snapshots WHERE ticker='CASY' "
            "ORDER BY snapshot_date DESC, fetched_at DESC LIMIT 1"
        )
        m = cur.fetchone()
        cur.execute(
            "SELECT buy_trade, sell_trade FROM hedgeye_risk_ranges "
            "WHERE ticker='CASY' ORDER BY signal_date DESC LIMIT 1"
        )
        r = cur.fetchone()
        price = float(m[0]) if m and m[0] else None
        zone  = "unknown"
        if r and r[0] and r[1] and price:
            zone = compute_zone(price, float(r[0]), float(r[1]))
        elif m and m[1] and m[2] and price:
            zone = compute_zone(price, float(m[1]), float(m[2]))
        trend = _norm(m[3]) if m else "neutral"
        mom   = _norm(m[4]) if m else "neutral"
        action, ctx = de._trend_momentum_gate(zone, side, trend, mom)
        print(f"  mfr_snapshot:  price={price} range={m[1]}-{m[2]} trend={m[3]} momentum={m[4]}" if m else "  (no MFR)")
        print(f"  rr:            {r}")
        print(f"  zone:          {zone}")
        print(f"  gate inputs:   zone={zone} side={side} trend={trend} momentum={mom}")
        print(f"  action:        {action}")
        print(f"  context:       {ctx}")

print()
print("=" * 78)
print("9) Live trade_recommendations for CASY (last 3 days)")
print("=" * 78)
with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT created_at, action, conviction, direction, reasoning "
            "FROM trade_recommendations WHERE ticker='CASY' "
            "AND created_at >= NOW() - interval '3 days' "
            "ORDER BY created_at DESC"
        )
        rs = cur.fetchall()
        if not rs: print("  (no CASY trade_recommendations in last 3 days)")
        for r in rs:
            print(f"  {r[0]} action={r[1]:6} conv={r[2]} dir={r[3]}")
            print(f"    {(r[4] or '')[:240]}")
