"""Simulate the new side-aware verb for every ETF Pro Short ticker.

For each of the 16 etf_pro_short tickers, pull latest MFR + RR data and
show:
  - current zone (under live edge-only logic)
  - LONG-side action (what live bot does today — wrong for shorts)
  - SHORT-side action (what the new gate will do — correct)
  - whether the verb actually changed

This is the diagnostic input the operator asked for. Read-only.
"""
from tools import active_slice as al
import db_pg
from price_monitor import compute_zone


def _norm(s):
    if not s: return "neutral"
    low = str(s).lower()
    if "bullish" in low: return "bullish"
    if "bearish" in low: return "bearish"
    return "neutral"


def gate_long(zone, trend, mom):
    if zone == "below_range":
        return ("SELL", "trend breakdown — avoid longs, consider shorts")
    if zone == "above_range":
        return ("BUY",  "trend continuation — add to longs")
    if zone == "bottom_edge":
        if trend == "bullish" and mom == "bullish":
            return ("BUY",   "bottom-edge scale-in (long)")
        if trend == "bullish":
            return ("WATCH", "bottom-edge but momentum diverging")
        if trend == "bearish":
            return ("AVOID", "bearish trend — don't catch knife")
        return ("WATCH", "neutral trend — wait")
    if zone == "top_edge":
        if trend == "bullish" and mom == "bullish":
            return ("HOLD",  "trend+momentum supportive — continuation")
        return ("SELL", "top-edge trim")
    return ("WATCH", "")


def gate_short(zone, trend, mom):
    if zone == "below_range":
        return ("SHORT", "trend continuation — add to short")
    if zone == "above_range":
        return ("COVER", "trend invalidated — close short")
    if zone == "top_edge":
        if trend == "bearish" and mom == "bearish":
            return ("SHORT", "top-edge short entry — trend+momentum confirm")
        if trend == "bearish":
            return ("WATCH", "top-edge but momentum diverging — wait")
        if trend == "bullish":
            return ("AVOID", "top-edge in bullish trend — don't short into strength")
        return ("WATCH", "top-edge but trend neutral — wait")
    if zone == "bottom_edge":
        if trend == "bearish" and mom == "bearish":
            return ("HOLD",  "trend+momentum confirming — short continuation")
        return ("COVER", "bottom-edge — take profit on short")
    return ("WATCH", "")


al.invalidate_cache()
bd = al.source_breakdown()
shorts = bd.get("etf_pro_short", [])

print(f"=== 16 ETF Pro Short tickers — verb under LONG-side vs new SHORT-side gate ===\n")
print(f"  {'TICKER':8} {'zone':12} {'trend':9} {'momentum':9} "
      f"{'LONG action':10} -> {'SHORT action':10}  {'changed?':9} "
      f"flags")
print(f"  {'-'*8} {'-'*12} {'-'*9} {'-'*9} {'-'*10}    {'-'*10}  {'-'*9} "
      f"{'-'*30}")

changed_count = 0
unchanged_count = 0
no_data_count = 0

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        for tk in shorts:
            cur.execute("""
                SELECT price, range_low, range_high, trend_signal, momentum_signal
                  FROM mfr_snapshots
                 WHERE ticker = %s
                 ORDER BY snapshot_date DESC, fetched_at DESC LIMIT 1
            """, (tk,))
            mfr = cur.fetchone()
            cur.execute("""
                SELECT buy_trade, sell_trade
                  FROM hedgeye_risk_ranges
                 WHERE ticker = %s ORDER BY signal_date DESC LIMIT 1
            """, (tk,))
            rr = cur.fetchone()

            flags = ", ".join(al.source_flags_for(tk))

            if not mfr or mfr[1] is None or mfr[2] is None:
                if rr and rr[0] is not None and rr[1] is not None:
                    zone = compute_zone(0, float(rr[0]), float(rr[1]))  # bogus price
                else:
                    print(f"  {tk:8} (no RR or MFR range data — short-circuit)            "
                          f"{flags}")
                    no_data_count += 1
                    continue

            price = float(mfr[0])
            # Prefer RR for zone — matches decision_engine
            if rr and rr[0] is not None and rr[1] is not None:
                zone = compute_zone(price, float(rr[0]), float(rr[1]))
            else:
                zone = compute_zone(price, float(mfr[1]), float(mfr[2]))

            trend = _norm(mfr[3])
            mom   = _norm(mfr[4])

            long_action,  _ = gate_long(zone, trend, mom)
            short_action, _ = gate_short(zone, trend, mom)

            changed = long_action != short_action
            if changed:
                changed_count += 1
            else:
                unchanged_count += 1
            mark = "* CHANGE" if changed else ""

            print(f"  {tk:8} {zone:12} {trend:9} {mom:9} "
                  f"{long_action:10} -> {short_action:10}  {mark:9} {flags[:50]}")

print()
print(f"Summary:")
print(f"  changed (verb is different under short-side gate): {changed_count}")
print(f"  unchanged (zone is mid_range, no data, or coincidentally same): {unchanged_count}")
print(f"  no MFR/RR data — already suppressed: {no_data_count}")
