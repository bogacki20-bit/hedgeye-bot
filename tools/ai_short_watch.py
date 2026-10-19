"""
ai_short_watch.py — stalk the AI-bubble shorts on bounces (Kris, 2026-10-19).

Watch list: ai_short_watch (migration 122), seeded from Hedgeye's Big Theme
"AI Bubble" deck (slide 53 bearish-TREND names; neoclouds first).

A name is IN THE ZONE when its own trend is still BEARISH (v_screener
trend_dir) and it has bounced into either
  - the top of its range: rp >= AI_SHORT_RP (default 0.80), or
  - its TRADE line (tv_trade, the bearish mirror = the line a bounce runs into):
    price >= tv_trade * (1 - AI_SHORT_TRADE_BAND) (default 1%).
That is Hedgeye's short setup: bearish TRADE/TREND, sold near the high end of
the range. Alerts fire on ENTERING the zone (crossing state in bot_state, first
sight seeds silently), one per name per day (book_alerts_fired). Facts only —
the operator decides; only the Individual account can short.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("ai_short_watch")

AI_SHORT_RP = float(os.getenv("AI_SHORT_RP", "0.80"))
AI_SHORT_TRADE_BAND = float(os.getenv("AI_SHORT_TRADE_BAND", "0.01"))
_STATE_KEY = "ai_short_watch_state"      # bot_state JSON {ticker: bool in_zone}


def in_zone(r: dict, rp_min: float = AI_SHORT_RP,
            band: float = AI_SHORT_TRADE_BAND) -> tuple[bool, str]:
    """Pure. (in_zone, why). Needs a BEARISH trend and a bounce."""
    if (r.get("trend_dir") or "").upper() != "BEARISH":
        return False, "trend not bearish"
    why = []
    rp, p, trd = r.get("rp"), r.get("price"), r.get("tv_trade")
    if rp is not None and float(rp) >= rp_min:
        why.append(f"rp {float(rp):.2f} ≥ {rp_min:.2f}")
    if p is not None and trd is not None and float(p) >= float(trd) * (1 - band):
        why.append(f"at/above TRADE {float(trd):,.2f}")
    return bool(why), " · ".join(why)


def detect(rows: list, prev: dict | None = None) -> tuple[list, dict]:
    """Pure. rows: {ticker, layer, severity_pct, price, rp, range_low,
    range_high, trend_dir, tv_trade, tv_trend, ss_side, held_short}.
    Fires on a known False -> True transition. Returns (alerts, state)."""
    prev = prev or {}
    out, state = [], {}
    for r in rows:
        t = r["ticker"]
        z, why = in_zone(r)
        state[t] = z
        if not z or t not in prev or prev[t]:
            continue
        p = float(r["price"])
        rng = (f"range {float(r['range_low']):,.2f}–{float(r['range_high']):,.2f}"
               if r.get("range_low") is not None and r.get("range_high") is not None
               else "range n/a")
        lv = []
        if r.get("tv_trade") is not None:
            lv.append(f"TRADE {float(r['tv_trade']):,.2f}")
        if r.get("tv_trend") is not None:
            lv.append(f"TREND {float(r['tv_trend']):,.2f}")
        sev = (f" · deck est. {float(r['severity_pct']):.0f}% if it breaks"
               if r.get("severity_pct") is not None else "")
        ss = f"Signal Strength: {r['ss_side']}" if r.get("ss_side") else "not on Signal Strength"
        held = " · you are ALREADY short" if r.get("held_short") else ""
        out.append({"type": "ai_short_watch", "ticker": t,
                    "line": f"🎯 AI SHORT WATCH {t} ({r.get('layer')}): bounce into "
                            f"the zone — {why}. Price {p:,.2f}, {rng}, "
                            f"{' · '.join(lv) or 'no TV lines'}, trend BEARISH. "
                            f"{ss}{sev}{held}. Short setup per process "
                            f"(Individual acct only); your rules decide."})
    return out, state


def _rows() -> list:
    import db_pg
    from tools.book_direction import book_sides
    try:
        sides = book_sides()
    except Exception:
        sides = {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT w.ticker, w.layer, w.severity_pct, v.price, v.range_pos,
                      v.range_low, v.range_high, COALESCE(v.trend_dir, w.deck_trend), v.tv_trade, v.tv_trend,
                      (SELECT string_agg(s.side, '/') FROM ss_roster_history s
                        WHERE s.ticker = w.ticker AND s.removed_on IS NULL)
                 FROM ai_short_watch w
                 LEFT JOIN v_screener v USING (ticker)
                WHERE w.active""")
        got = cur.fetchall()
    rows = []
    for t, layer, sev, p, rp, lo, hi, td, trd, trn, ss in got:
        if p is None:
            continue
        rows.append({"ticker": t, "layer": layer, "severity_pct": sev,
                     "price": float(p), "rp": float(rp) if rp is not None else None,
                     "range_low": lo, "range_high": hi, "trend_dir": td,
                     "tv_trade": trd, "tv_trend": trn,
                     "ss_side": f"{ss} list" if ss else None,
                     "held_short": (sides.get(t) or {}).get("side") == "short"})
    return rows


def run_ai_short_watch(dry_run: bool = False) -> dict:
    """One cycle: assemble, detect, dedupe, send, persist. Loud dict."""
    import db_pg
    from tools.book_alerts import _load_state, _save_state, _already_fired
    summary = {"watched": 0, "in_zone": [], "sent": [], "dry_run": dry_run}
    if os.getenv("AI_SHORT_WATCH_ENABLED", "1") != "1":
        summary["disabled"] = True
        return summary
    try:
        rows = _rows()
    except Exception as e:
        log.exception("ai_short_watch: assembly failed")
        summary["error"] = str(e)
        return summary
    summary["watched"] = len(rows)
    alerts, state = detect(rows, _load_state(_STATE_KEY))
    summary["in_zone"] = sorted(t for t, z in state.items() if z)
    if dry_run:
        summary["sent"] = [f"{a['ticker']} [dry]" for a in alerts]
        summary["lines"] = [a["line"] for a in alerts]
        return summary
    if alerts:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for a in alerts:
                if _already_fired(cur, a["ticker"], a["type"]):
                    continue
                cur.execute("INSERT INTO book_alerts_fired (ticker, alert_type, fired_on, "
                            "details) VALUES (%s,%s,CURRENT_DATE,%s) ON CONFLICT DO NOTHING",
                            (a["ticker"], a["type"], a["line"]))
                if not (cur.rowcount or 0):
                    continue
                try:
                    from notifier import send_telegram
                    send_telegram(f"🎯 AI SHORT WATCH {a['ticker']}", a["line"])
                    summary["sent"].append(a["ticker"])
                except Exception as e:
                    log.warning("ai_short_watch telegram failed: %s", e)
            conn.commit()
    _save_state(_STATE_KEY, state)
    return summary


def board() -> str:
    """On-demand table: every watch name, where it sits, zone or not."""
    rows = sorted(_rows(), key=lambda r: (r.get("severity_pct") or 0, r["ticker"]))
    out = ["AI SHORT WATCH — bearish-trend names, bounce zone = rp ≥ "
           f"{AI_SHORT_RP:.2f} or at TRADE"]
    for r in rows:
        z, why = in_zone(r)
        rp = f"{r['rp']:.2f}" if r.get("rp") is not None else " n/a"
        trd = f"{float(r['tv_trade']):,.2f}" if r.get("tv_trade") is not None else "n/a"
        out.append(f"{'🎯' if z else '  '} {r['ticker']:<6} {r['layer']:<12} px {r['price']:>9,.2f} "
                   f"rp {rp} TRADE {trd} trend {r.get('trend_dir') or '?'}"
                   f"{' · SS ' + r['ss_side'] if r.get('ss_side') else ''}")
    return "\n".join(out)


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="tools.ai_short_watch")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--board", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    if a.board:
        print(board())
        sys.exit(0)
    s = run_ai_short_watch(dry_run=a.dry_run)
    for ln in s.pop("lines", []):
        print(ln)
    print(s)
