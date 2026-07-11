"""
book_alerts.py — book management v1 alerts (build queue item 5).

Watches the operator's ACTUAL holdings — exempt from the 20/80 universe
filter (that filter finds candidates; this pipeline manages commitments).

Alert types (facts + the operator's own rulebook line; never advice):

  DIP  — held LONG, trend intact (BULLISH on the adjusted signal), and
         range-position has RETREATED >= BOOK_DIP_DELTA (default 0.25) from
         its 5-day maximum. A dip is a retreat, not a location — fixed
         floors miss real dips (XLV proof case: 0.895 -> 0.705 in a session
         = alert; its Tue/Wed dips near 0.50 never approached a 0.35 floor).
  RIP  — mirror for held SHORTS: trend intact (BEARISH) and rp has risen
         >= delta from its 5-day minimum (cover-into-strength check).
  TREND_FLIP — the DAY the adjusted trend turns against the position
         ("SBIT thesis break: BTC flipped BULLISH"). State memory in
         bot_state; only the transition alerts, not the standing condition.

Wrappers: trend is the UNDERLYING's, inverted where the linkage says so —
the same adjusted trend_dir SCREEN displays (via tools.screener helpers).

Dedup: one alert per (ticker, type) per day via book_alerts_fired (059).
Env knobs: BOOK_DIP_DELTA (default 0.25), BOOK_ALERTS_ENABLED (default 1).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date

log = logging.getLogger("book_alerts")

BOOK_DIP_DELTA = float(os.getenv("BOOK_DIP_DELTA", "0.25"))
_STATE_KEY = "book_trend_state"          # bot_state JSON {ticker: trend_dir}
_RETREAT_KEY = "book_retreat_state"      # bot_state JSON {ticker: retreat}


# ─────────────────────────── pure logic ───────────────────────────

def detect(rows: list, delta: float = BOOK_DIP_DELTA,
           prev_trends: dict | None = None,
           prev_retreats: dict | None = None) -> list:
    """Pure detector, unit-testable. Each row:
      {ticker, side ('long'/'short'), trend_dir, rp_now, rp_5d_max, rp_5d_min,
       wrap (optional underlying note)}
    Returns (alerts, retreats): alert dicts {type, ticker, line} + the
    current retreat measure per ticker (for crossing-state persistence).

    CROSSING semantics (operator spec: a dip is a RETREAT — an event, not a
    location): dip/rip fire only when the retreat measure crosses the delta
    threshold vs the previous cycle. Unknown previous state (first run,
    fresh deploy) seeds silently — same rule as trend flips — so a deploy
    never floods 20 standing dips at once."""
    prev_trends = prev_trends or {}
    prev_retreats = prev_retreats or {}
    out = []
    retreats: dict = {}
    for r in rows:
        t = r["ticker"]
        side = r.get("side")
        td = r.get("trend_dir") or ""
        rp = r.get("rp_now")
        wrap = f" ({r['wrap']})" if r.get("wrap") else ""

        # trend flip — only on transition from a KNOWN prior trend
        prev = prev_trends.get(t)
        against = ((side == "long" and td == "BEARISH") or
                   (side == "short" and td == "BULLISH"))
        was_against = ((side == "long" and prev == "BEARISH") or
                       (side == "short" and prev == "BULLISH"))
        if against and prev and not was_against:
            out.append({"type": "trend_flip", "ticker": t,
                        "line": f"⚡ THESIS CHECK {t}{wrap}: trend flipped "
                                f"{td} against your {side.upper()} "
                                f"(was {prev}). Your rules decide."})

        if rp is None:
            continue
        if side == "long" and td == "BULLISH" and r.get("rp_5d_max") is not None:
            drop = float(r["rp_5d_max"]) - float(rp)
            retreats[t] = round(drop, 4)
            prev = prev_retreats.get(t)
            if drop >= delta and prev is not None and float(prev) < delta:
                out.append({"type": "dip", "ticker": t,
                            "line": f"📉 DIP {t}{wrap}: rp {float(rp):.2f}, "
                                    f"retreated {drop:.2f} from 5d high "
                                    f"{float(r['rp_5d_max']):.2f}, trend intact "
                                    f"BULLISH — tranche-add zone per your "
                                    f"rules; check TREND/TRADE levels."})
        elif side == "short" and td == "BEARISH" and r.get("rp_5d_min") is not None:
            rise = float(rp) - float(r["rp_5d_min"])
            retreats[t] = round(rise, 4)
            prev = prev_retreats.get(t)
            if rise >= delta and prev is not None and float(prev) < delta:
                out.append({"type": "rip", "ticker": t,
                            "line": f"📈 RIP {t}{wrap}: rp {float(rp):.2f}, "
                                    f"bounced {rise:.2f} off 5d low "
                                    f"{float(r['rp_5d_min']):.2f}, trend intact "
                                    f"BEARISH — add-to-short / cover-discipline "
                                    f"zone per your rules."})
    return out, retreats


# ─────────────────────────── data assembly ───────────────────────────

def _book_rows() -> list:
    """Holdings with adjusted trend (screener's own COALESCE + BTCQ + wrapper
    inversion), current rp, and 5-day rp extremes from mfr_snapshots."""
    from tools.book_direction import book_sides
    from tools.screener import (_fetch_source_slice, _apply_btcquant_trend,
                                _apply_wrapper_trend)
    import db_pg

    sides = book_sides()
    held = sorted(t for t, v in sides.items() if v.get("side") in ("long", "short"))
    if not held:
        return []
    slice_ = _fetch_source_slice(held, None)
    _apply_btcquant_trend(slice_)
    _apply_wrapper_trend(slice_)
    by_t = {r["ticker"]: r for r in slice_}

    ext: dict = {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ticker,
                      max((price - range_low) / NULLIF(range_high - range_low, 0)),
                      min((price - range_low) / NULLIF(range_high - range_low, 0))
               FROM mfr_snapshots
               WHERE ticker = ANY(%s)
                 AND snapshot_date >= CURRENT_DATE - 7
               GROUP BY ticker""", (held,))
        for t, hi, lo in cur.fetchall():
            ext[t] = (float(hi) if hi is not None else None,
                      float(lo) if lo is not None else None)

    rows = []
    for t in held:
        r = by_t.get(t)
        if not r:
            continue                    # no MFR coverage — DARK, nothing to judge
        hi, lo = ext.get(t, (None, None))
        wrap = None
        if r.get("_wrap"):
            w = r["_wrap"]
            wrap = f"u:{w['underlying']}{'↯inv' if w['inverse'] else ''}"
        # FRAME RULE (the SBIT double-flip bug, 2026-07-11): compare the RAW
        # holding side against the linkage-ADJUSTED trend — both flip together
        # on inverse wrappers, so the verdict is frame-invariant. Using the
        # exposure side here double-flips: intact theses get flagged, broken
        # ones don't. raw_side == side for everything non-wrapped.
        rows.append({"ticker": t, "side": sides[t].get("raw_side") or sides[t]["side"],
                     "trend_dir": r.get("trend_dir"),
                     "rp_now": (float(r["range_pos"])
                                if r.get("range_pos") is not None else None),
                     "rp_5d_max": hi, "rp_5d_min": lo, "wrap": wrap})
    return rows


# ─────────────────────────── state + dedup + send ───────────────────────────

def _load_state(key: str) -> dict:
    import db_pg
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
            r = cur.fetchone()
            return json.loads(r[0]) if r and r[0] else {}
    except Exception as e:
        log.warning("book_alerts: state read failed (%s): %s", key, e)
        return {}


def _save_state(key: str, data: dict) -> None:
    import db_pg
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO bot_state (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                     SET value = EXCLUDED.value, updated_at = NOW()""",
                (key, json.dumps(data)))
            c.commit()
    except Exception as e:
        log.warning("book_alerts: state write failed (%s): %s", key, e)


def _already_fired(cur, ticker: str, alert_type: str) -> bool:
    cur.execute("SELECT 1 FROM book_alerts_fired WHERE ticker=%s AND "
                "alert_type=%s AND fired_on=CURRENT_DATE", (ticker, alert_type))
    return cur.fetchone() is not None


def run_book_alerts(dry_run: bool = False) -> dict:
    """One cycle: assemble, detect, dedup, send, persist state. Loud dict."""
    import db_pg
    summary = {"held": 0, "alerts": 0, "sent": [], "deduped": 0,
               "dry_run": dry_run}
    if os.getenv("BOOK_ALERTS_ENABLED", "1") != "1":
        summary["disabled"] = True
        return summary
    try:
        rows = _book_rows()
    except Exception as e:
        log.exception("book_alerts: assembly failed")
        summary["error"] = str(e)
        return summary
    summary["held"] = len(rows)
    prev_trends = _load_state(_STATE_KEY)
    prev_retreats = _load_state(_RETREAT_KEY)
    alerts, retreats = detect(rows, prev_trends=prev_trends,
                              prev_retreats=prev_retreats)
    summary["alerts"] = len(alerts)
    summary["in_zone"] = sum(1 for v in retreats.values()
                             if v >= BOOK_DIP_DELTA)   # standing, not alerted

    if not dry_run and alerts:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for a in alerts:
                if _already_fired(cur, a["ticker"], a["type"]):
                    summary["deduped"] += 1
                    continue
                cur.execute(
                    "INSERT INTO book_alerts_fired (ticker, alert_type, "
                    "fired_on, details) VALUES (%s,%s,CURRENT_DATE,%s) "
                    "ON CONFLICT DO NOTHING", (a["ticker"], a["type"], a["line"]))
                if not (cur.rowcount or 0):
                    summary["deduped"] += 1
                    continue
                try:
                    from notifier import send_telegram
                    send_telegram(f"📗 BOOK {a['type'].upper()} {a['ticker']}",
                                  a["line"])
                    summary["sent"].append(f"{a['type']}:{a['ticker']}")
                except Exception as e:
                    log.warning("book_alerts telegram failed: %s", e)
            conn.commit()
    elif dry_run:
        summary["sent"] = [f"{a['type']}:{a['ticker']} [dry]" for a in alerts]

    # persist state AFTER alerting so flips/crossings fire exactly once
    if not dry_run:
        _save_state(_STATE_KEY, {r["ticker"]: r.get("trend_dir") for r in rows
                                 if r.get("trend_dir")})
        _save_state(_RETREAT_KEY, retreats)
    return summary


if __name__ == "__main__":
    import argparse, sys
    ap = argparse.ArgumentParser(prog="tools.book_alerts")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    s = run_book_alerts(dry_run=a.dry_run)
    print(s)
    for line in s.get("sent", []):
        print(" ", line)
    sys.exit(0)
