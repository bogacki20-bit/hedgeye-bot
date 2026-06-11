"""Diagnose missing ETF Pro Short alerts.

Read-only — for each ticker in the current ETF Pro Short bucket, count
recent alerts and decisions, identify zone today, and report whether the
ticker hit an edge that SHOULD have fired.
"""
import db_pg
from tools import active_slice as al
from price_monitor import compute_zone

al.invalidate_cache()
bd = al.source_breakdown()
shorts = bd.get("etf_pro_short", [])
longs  = bd.get("etf_pro_long",  [])

print(f"=== Current ETF Pro buckets ===")
print(f"  etf_pro_short ({len(shorts)}): {shorts}")
print(f"  etf_pro_long  ({len(longs)}):  {longs[:10]}... +{max(0,len(longs)-10)} more")
print()

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        print("=" * 78)
        print("For each ETF Pro Short ticker — last 7d activity")
        print("=" * 78)
        print(f"  {'TICKER':8} {'mfr_zone':12} {'rr_pos%':>7} {'mfr_pos%':>7} "
              f"{'trend':10} {'momentum':16} {'alerts7d':>9} {'recs7d':>7}")
        print(f"  {'-'*8} {'-'*12} {'-'*7} {'-'*7} {'-'*10} {'-'*16} {'-'*9} {'-'*7}")

        for tk in shorts:
            cur.execute("""
                SELECT price, range_low, range_high, trend_signal, momentum_signal
                  FROM mfr_snapshots
                 WHERE ticker = %s
                 ORDER BY snapshot_date DESC, fetched_at DESC
                 LIMIT 1
            """, (tk,))
            r = cur.fetchone()
            if not r:
                mfr_pos, mfr_zone, trend, mom = None, "(no MFR)", None, None
                price = None
            else:
                price, mfr_lo, mfr_hi, trend, mom = r
                mfr_zone = compute_zone(float(price), float(mfr_lo), float(mfr_hi)) if (mfr_lo and mfr_hi) else "(no range)"
                mfr_pos = ((float(price) - float(mfr_lo)) / (float(mfr_hi) - float(mfr_lo)) * 100
                           if mfr_lo and mfr_hi and mfr_hi > mfr_lo else None)

            cur.execute("""
                SELECT buy_trade, sell_trade FROM hedgeye_risk_ranges
                 WHERE ticker = %s
                 ORDER BY signal_date DESC LIMIT 1
            """, (tk,))
            rr = cur.fetchone()
            rr_pos = None
            if rr and rr[0] is not None and rr[1] is not None and price is not None:
                rl, rh = float(rr[0]), float(rr[1])
                if rh > rl:
                    rr_pos = (float(price) - rl) / (rh - rl) * 100

            cur.execute("""
                SELECT COUNT(*) FROM alerts_fired
                 WHERE ticker=%s AND signal_date >= CURRENT_DATE - interval '7 days'
            """, (tk,))
            alerts7d = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM trade_recommendations
                 WHERE ticker=%s AND created_at >= NOW() - interval '7 days'
            """, (tk,))
            recs7d = cur.fetchone()[0]

            trend_short = (trend or "").replace("trend", "")[:9]
            mom_short   = (mom or "").replace("momentum", "")[:15]
            rr_str  = f"{rr_pos:7.1f}" if rr_pos is not None else "    n/a"
            mfr_str = f"{mfr_pos:7.1f}" if mfr_pos is not None else "    n/a"
            print(f"  {tk:8} {mfr_zone:12} {rr_str} {mfr_str} {trend_short:10} {mom_short:16} {alerts7d:>9} {recs7d:>7}")

        print()
        print("=" * 78)
        print("Recent trade_recommendations for ANY etf_pro_short ticker (last 7d)")
        print("=" * 78)
        if shorts:
            cur.execute("""
                SELECT created_at, ticker, action, conviction, reasoning
                  FROM trade_recommendations
                 WHERE ticker = ANY(%s)
                   AND created_at >= NOW() - interval '7 days'
                 ORDER BY created_at DESC LIMIT 30
            """, (shorts,))
            rows = cur.fetchall()
            if not rows:
                print("  (none)")
            for r in rows:
                print(f"  {r[0]} {r[1]:6} action={r[2]:5} conv={r[3]:8}")
                print(f"    {(r[4] or '')[:180]}")

        print()
        print("=" * 78)
        print("Today's source_flags_for() result for each ETF Pro Short ticker")
        print("=" * 78)
        for tk in shorts:
            flags = al.source_flags_for(tk)
            print(f"  {tk:8} {flags}")
