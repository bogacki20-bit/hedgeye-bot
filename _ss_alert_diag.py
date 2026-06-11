"""Comprehensive Signal Strength alert diagnostic.

For each ticker in the current SS bucket, surface:
  - source_flags / side / polling membership
  - mfr_snapshot today: price, range, trend, momentum, % of range, zone
  - alerts_fired today (count + source label tagging)
  - trade_recommendations today (the Telegram-fired version)

Then call out:
  (a) SS tickers in polling at edge zones but NOT firing → real bug
  (b) SS tickers firing but tagged wrong label (e.g. [Quad] not [SS]) → label bug
  (c) SS tickers not in polling at all → universe inclusion bug
"""
import os
import importlib

os.environ.setdefault("CURRENT_MONTHLY_QUAD_OVERRIDE",   "Quad 2")
os.environ.setdefault("CURRENT_QUARTERLY_QUAD_OVERRIDE", "Quad 3")

import db_pg
import tools.active_slice as al
import decision_engine as de
from price_monitor import compute_zone

importlib.reload(al); importlib.reload(de); al.invalidate_cache()


def n(s):
    if not s: return "neutral"
    s = str(s).lower()
    if "bullish" in s: return "bullish"
    if "bearish" in s: return "bearish"
    return "neutral"


bd = al.source_breakdown()
ss_bucket = sorted(bd.get("signal_strength", []))
stale_ss  = sorted(bd.get("stale_signal_strength", []))
polling_universe = set(al.polling_universe())

print(f"=== A. Signal Strength buckets ===")
print(f"  fresh signal_strength bucket: {len(ss_bucket)} tickers")
print(f"  {ss_bucket}")
print(f"  stale_signal_strength bucket: {len(stale_ss)} tickers")
if stale_ss: print(f"  {stale_ss}")
print(f"  polling universe size: {len(polling_universe)}")
print(f"  SS bucket ∩ polling_universe: {sorted(set(ss_bucket) & polling_universe)}")
print(f"  SS bucket - polling_universe: {sorted(set(ss_bucket) - polling_universe)}")
print()

# Pull live data for each SS ticker
with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT MAX(snapshot_date), COUNT(DISTINCT ticker)
              FROM hedgeye_signal_strength
             WHERE snapshot_date >= CURRENT_DATE - interval '3 days'
        """)
        r = cur.fetchone()
        print(f"=== B. hedgeye_signal_strength state ===")
        print(f"  latest snapshot_date in last 3d: {r[0]}  distinct tickers: {r[1]}")
        cur.execute("""
            SELECT snapshot_date, ticker, is_added_today, is_removed_today
              FROM hedgeye_signal_strength
             WHERE snapshot_date >= CURRENT_DATE - interval '3 days'
             ORDER BY snapshot_date DESC, ticker
        """)
        rows = cur.fetchall()
        print(f"  recent SS rows:")
        for r in rows:
            print(f"    {r}")

        print()
        print(f"=== C. Per-ticker matrix for the {len(ss_bucket)} fresh SS tickers ===")
        print(f"  {'TICKER':8} {'side':12} {'in_uni':6} {'zone':12} {'%RR':>6} {'trend':9} {'mom':9} "
              f"{'alerts_today':>12} {'recs_today':>10} flags")

        for tk in ss_bucket:
            flags = al.source_flags_for(tk)
            side  = de._ticker_side(flags)
            in_uni = tk in polling_universe

            cur.execute("""
                SELECT price, range_low, range_high, trend_signal, momentum_signal
                  FROM mfr_snapshots WHERE ticker=%s
                 ORDER BY snapshot_date DESC, fetched_at DESC LIMIT 1
            """, (tk,))
            m = cur.fetchone()
            cur.execute("""
                SELECT buy_trade, sell_trade FROM hedgeye_risk_ranges
                 WHERE ticker=%s ORDER BY signal_date DESC LIMIT 1
            """, (tk,))
            rr = cur.fetchone()
            price = float(m[0]) if m and m[0] else None
            zone = "unknown"
            pct = None
            if rr and rr[0] and rr[1] and price:
                zone = compute_zone(price, float(rr[0]), float(rr[1]))
                pct  = (price - float(rr[0])) / (float(rr[1]) - float(rr[0])) * 100
            elif m and m[1] and m[2] and price:
                zone = compute_zone(price, float(m[1]), float(m[2]))
                pct  = (price - float(m[1])) / (float(m[2]) - float(m[1])) * 100
            trend = n(m[3]) if m else "neutral"
            mom   = n(m[4]) if m else "neutral"

            cur.execute("""
                SELECT COUNT(*) FROM alerts_fired
                 WHERE ticker=%s AND fired_at::date = CURRENT_DATE
            """, (tk,))
            n_alerts = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM trade_recommendations
                 WHERE ticker=%s AND created_at::date = CURRENT_DATE
            """, (tk,))
            n_recs = cur.fetchone()[0]

            pct_str = f"{pct:.0f}" if pct is not None else "n/a"
            print(f"  {tk:8} {side:12} {str(in_uni):6} {zone:12} {pct_str:>6} {trend:9} {mom:9} "
                  f"{n_alerts:>12} {n_recs:>10} {flags}")

        # ─── label-priority audit: did SS tickers get tagged correctly? ───
        print()
        print(f"=== D. Source-label tagging for SS tickers firing today ===")
        cur.execute("""
            SELECT ticker, action, conviction, LEFT(reasoning, 100) AS rsn
              FROM trade_recommendations
             WHERE ticker = ANY(%s)
               AND created_at::date = CURRENT_DATE
             ORDER BY created_at DESC LIMIT 30
        """, (ss_bucket,))
        rows = cur.fetchall()
        if not rows:
            print("  (no SS-ticker trade_recommendations today)")
        for r in rows:
            label = "[?]"
            if r[3] and r[3].startswith("["):
                label = r[3].split("]")[0] + "]"
            ok = "✓" if "[Signal Strength]" in label else "✗"
            print(f"  {ok} {r[0]:8} action={r[1]:5} label={label:32} ")

        # Specific cases — SS tickers at edge today
        print()
        print(f"=== E. SS tickers at edge zones today — should have fired ===")
        for tk in ss_bucket:
            cur.execute("SELECT price,range_low,range_high,trend_signal,momentum_signal FROM mfr_snapshots WHERE ticker=%s ORDER BY snapshot_date DESC,fetched_at DESC LIMIT 1", (tk,))
            m = cur.fetchone()
            cur.execute("SELECT buy_trade,sell_trade FROM hedgeye_risk_ranges WHERE ticker=%s ORDER BY signal_date DESC LIMIT 1", (tk,))
            rr = cur.fetchone()
            if not m or not m[0]: continue
            price = float(m[0])
            zone = "unknown"
            if rr and rr[0] and rr[1]:
                zone = compute_zone(price, float(rr[0]), float(rr[1]))
            elif m[1] and m[2]:
                zone = compute_zone(price, float(m[1]), float(m[2]))
            if zone in ("bottom_edge","top_edge","below_range","above_range"):
                flags = al.source_flags_for(tk)
                side  = de._ticker_side(flags)
                action, ctx = de._trend_momentum_gate(zone, side, n(m[3]), n(m[4]))
                cur.execute("SELECT COUNT(*) FROM trade_recommendations WHERE ticker=%s AND created_at::date = CURRENT_DATE", (tk,))
                n_today = cur.fetchone()[0]
                fired_str = f"{n_today} alerts today" if n_today else "NO ALERT TODAY"
                print(f"  {tk:8} zone={zone:12} side={side:8} expected={action:6} actual={fired_str}")
                print(f"    ctx: {ctx}")
