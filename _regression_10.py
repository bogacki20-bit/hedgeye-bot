"""Regression suite — 10 operator-named tickers vs the bundled fixes.

For each ticker:
  1. MFR snapshots (last 5 days) — price / range / trend / momentum / walls / IV
  2. Source flag membership (which buckets it lives in)
  3. Zone today + percentage-through-range (RR + MFR)
  4. OLD logic action (pre-bundled-commit, naive top-third/bottom-third)
  5. NEW logic action (post-47d8810: strict-current universe, trend-following
                       framework, trend+momentum gate, side-aware verbs)
  6. Keith's actual PS actions on this ticker in last 7 days (where available)

Read-only diagnostic. Does NOT call Anthropic — uses the deterministic
Python helpers (_ticker_side, _trend_momentum_gate, compute_zone) so
the verdict is what the LIVE scanner cycle would produce.
"""
import importlib

import db_pg
from price_monitor import compute_zone
import tools.active_slice as al
import decision_engine as de

importlib.reload(al)
al.invalidate_cache()

TICKERS = ["BUXX", "USD", "UUP", "DXY", "TSN", "BITCOIN",
           "CAD/USD", "NVDA", "HYG", "XME"]


def _norm_mfr(s):
    if not s: return "neutral"
    low = str(s).lower()
    if "bullish" in low: return "bullish"
    if "bearish" in low: return "bearish"
    return "neutral"


def _old_gate(zone):
    """Pre-bundled-commit naive logic: top_edge -> SELL (mean revert),
    bottom_edge -> BUY, above_range -> SELL (the bug — was "fade"),
    below_range -> BUY (the bug — was "contrarian add"). This is what
    the operator saw."""
    return {
        "below_range": "BUY (contrarian add) — WRONG",
        "bottom_edge": "BUY",
        "mid_range":   "WATCH",
        "top_edge":    "SELL (top-edge fade)",
        "above_range": "SELL (range break above, fade) — WRONG",
        "unknown":     "WATCH",
    }.get(zone, "WATCH")


def _row_mfr(cur, ticker):
    cur.execute("""
        SELECT snapshot_date, price, range_low, range_high,
               trend_signal, momentum_signal,
               call_wall_mfr, put_wall_mfr, iv30_mfr,
               daily_pct_change
          FROM mfr_snapshots
         WHERE ticker = %s
         ORDER BY snapshot_date DESC LIMIT 5
    """, (ticker,))
    return cur.fetchall()


def _row_rr(cur, ticker):
    cur.execute("""
        SELECT signal_date, trend, buy_trade, sell_trade
          FROM hedgeye_risk_ranges
         WHERE ticker = %s
         ORDER BY signal_date DESC LIMIT 1
    """, (ticker,))
    return cur.fetchone()


def _ps_actions_recent(cur, ticker, days=7):
    cur.execute("""
        SELECT action_date, action, bps,
               LEFT(COALESCE(commentary_raw,''),120) AS commentary
          FROM hedgeye_portfolio_actions
         WHERE ticker = %s
           AND action_date >= CURRENT_DATE - (%s || ' days')::interval
         ORDER BY action_date DESC
    """, (ticker, str(days)))
    return cur.fetchall()


def _pct(price, lo, hi):
    if price is None or lo is None or hi is None: return None
    try:
        rng = float(hi) - float(lo)
        if rng <= 0: return None
        return (float(price) - float(lo)) / rng * 100.0
    except Exception:
        return None


print("=" * 88)
print("REGRESSION SUITE — 10 tickers vs the bundled commit")
print(f"  Live commit: 47d8810 (side-aware verbs)")
print(f"  Universe size now: {len(al.polling_universe())}")
print("=" * 88)

with db_pg.get_conn() as conn:
    with conn.cursor() as cur:
        for tk in TICKERS:
            print()
            print("─" * 88)
            print(f"  {tk}")
            print("─" * 88)

            # MFR
            mfr_rows = _row_mfr(cur, tk)
            if not mfr_rows:
                print("  mfr_snapshots: (NO ROWS — ticker not in MFR watchlist)")
            else:
                print("  mfr_snapshots (last 5 days):")
                print(f"    {'date':12} {'price':>10} {'range':>22} "
                      f"{'%RR':>6} {'trend':10} {'momentum':16} "
                      f"{'cw':>6} {'pw':>6} iv30")
                for r in mfr_rows:
                    pos = _pct(r[1], r[2], r[3])
                    rng = f"{r[2]}-{r[3]}" if r[2] is not None else "n/a"
                    print(f"    {str(r[0]):12} {str(r[1]):>10} {rng:>22} "
                          f"{(f'{pos:.1f}' if pos is not None else 'n/a'):>6} "
                          f"{str(r[4])[:10]:10} {str(r[5])[:16]:16} "
                          f"{str(r[6]):>6} {str(r[7]):>6} {r[8]}")

            # RR
            rr = _row_rr(cur, tk)
            print(f"\n  Hedgeye Risk Range latest: {rr if rr else '(no RR data)'}")

            # Source flags
            flags = al.source_flags_for(tk)
            side  = de._ticker_side(flags)
            print(f"  source_flags: {flags}")
            print(f"  side (per _ticker_side): {side}")

            # Zone today — RR preferred, MFR fallback
            mfr_latest = mfr_rows[0] if mfr_rows else None
            price = float(mfr_latest[1]) if mfr_latest and mfr_latest[1] else None
            zone  = "unknown"
            pct_basis = "none"
            if rr and rr[2] is not None and rr[3] is not None and price is not None:
                zone = compute_zone(price, float(rr[2]), float(rr[3]))
                pct_basis = "RR"
            elif mfr_latest and mfr_latest[2] is not None and mfr_latest[3] is not None:
                zone = compute_zone(price, float(mfr_latest[2]), float(mfr_latest[3]))
                pct_basis = "MFR"
            print(f"  zone today: {zone} ({pct_basis}-based)")

            # OLD logic
            old_action = _old_gate(zone)
            print(f"\n  OLD action (pre-47d8810):  {old_action}")

            # NEW logic — call the live gate
            trend = _norm_mfr(mfr_latest[4]) if mfr_latest else "neutral"
            mom   = _norm_mfr(mfr_latest[5]) if mfr_latest else "neutral"
            new_action, new_ctx = de._trend_momentum_gate(zone, side, trend, mom)
            print(f"  NEW action (post-47d8810): {new_action}  — {new_ctx}")
            print(f"    (gate inputs: zone={zone} side={side} trend={trend} momentum={mom})")

            # Keith's PS actions
            ps = _ps_actions_recent(cur, tk, days=14)
            if ps:
                print(f"\n  Keith's recent PS actions on {tk}:")
                for r in ps:
                    print(f"    {r[0]} {r[1]:7} {r[2]:>4}bps  {r[3]}")
