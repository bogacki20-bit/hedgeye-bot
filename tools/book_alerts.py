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
  TRADE_BREAK / TREND_BREAK / TREND_RECLAIM (2026-10-17) — the held name's
         live price crosses its own TradingView TRADE / TREND line (tv_trade /
         tv_trend from v_screener; longs below, shorts above). Keith's rule:
         breaks TRADE -> stop buying / take it to the minimum; breaks TREND ->
         it's a stop. Intraday crosses are labelled as such (his rule counts
         the CLOSE); the first cycle after 16:00 ET sends one CLOSE summary of
         every held name below trade / trend.

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
_LEVEL_KEY = "book_level_state"          # bot_state JSON {ticker: zone}

# zone severity: a move to a WORSE zone alerts; trend_break -> ok reclaims
_ZONES = {"ok": 0, "trade_break": 1, "trend_break": 2}


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


def level_zone(side: str, price, trade, trend, basis) -> str | None:
    """Pure: where a held position sits vs its own TV lines.
    'ok' | 'trade_break' | 'trend_break' | None (no price / no trend line).

    LONG, bull basis: below trend = trend_break, below trade = trade_break.
    LONG, bear basis (TV trend already BEARISH): below the trend line =
      trend_break; above it the name is testing a flip but the trend has not
      turned, so it stays trade_break (caution), never 'ok'.
    SHORT mirrors it: above trend (or a bull-basis trend) is against you."""
    if price is None or trend is None:
        return None
    p, tr = float(price), float(trend)
    td = float(trade) if trade is not None else None
    bear = (basis or "").startswith("bear")
    if side == "long":
        if p < tr:
            return "trend_break"
        if bear:
            return "trade_break"
        return "trade_break" if td is not None and p < td else "ok"
    if side == "short":
        if p > tr:
            return "trend_break"
        if not bear:
            return "trade_break"
        return "trade_break" if td is not None and p > td else "ok"
    return None


def detect_levels(rows: list, prev_zones: dict | None = None,
                  at_close: bool = False) -> tuple[list, dict]:
    """Pure detector for TRADE/TREND line crosses. Each row:
      {ticker, side, price, tv_trade, tv_trend, tv_basis, tv_bar_date}
    Fires only on a TRANSITION from a known prior zone (first sight seeds
    silently, same rule as trend flips). Returns (alerts, zones)."""
    prev_zones = prev_zones or {}
    out, zones = [], {}
    when = "on the close" if at_close else "intraday (Keith's rule counts the close)"
    for r in rows:
        t, side = r["ticker"], r.get("side")
        z = level_zone(side, r.get("price"), r.get("tv_trade"),
                       r.get("tv_trend"), r.get("tv_basis"))
        if z is None:
            continue
        zones[t] = z
        prev = prev_zones.get(t)
        if prev is None or prev not in _ZONES or z == prev:
            continue
        p = float(r["price"])
        tr = float(r["tv_trend"])
        td = r.get("tv_trade")
        lv = (f"price {p:,.2f} · trade {float(td):,.2f} · trend {tr:,.2f}"
              if td is not None else f"price {p:,.2f} · trend {tr:,.2f}")
        asof = f" (lines as of {r['tv_bar_date']})" if r.get("tv_bar_date") else ""
        if (r.get("tv_basis") or "").startswith("bear"):
            lv += " · trend dir BEARISH"
        elif side == "short":
            lv += " · trend dir BULLISH"
        if _ZONES[z] > _ZONES[prev]:
            if z == "trend_break":
                out.append({"type": "trend_break", "ticker": t,
                            "line": f"🛑 TREND BREAK {t} ({side.upper()}) {when}: "
                                    f"{lv}{asof}. Rulebook: breaks TREND -> it's a stop."})
            else:
                out.append({"type": "trade_break", "ticker": t,
                            "line": f"⚠️ TRADE BREAK {t} ({side.upper()}) {when}: "
                                    f"{lv}{asof}. Rulebook: breaks TRADE -> stop "
                                    f"buying / take it to the minimum."})
        elif prev == "trend_break" and z == "ok":
            out.append({"type": "trend_reclaim", "ticker": t,
                        "line": f"✅ RECLAIMED {t} ({side.upper()}) {when}: back "
                                f"above trade and trend — {lv}{asof}."})
    return out, zones


def close_summary(rows: list) -> str | None:
    """One end-of-day message: every held name below trend / below trade."""
    tb, rb = [], []
    for r in rows:
        z = level_zone(r.get("side"), r.get("price"), r.get("tv_trade"),
                       r.get("tv_trend"), r.get("tv_basis"))
        if z == "trend_break":
            d = " (trend BEARISH)" if (r.get("tv_basis") or "").startswith("bear") else ""
            tb.append(f"{r['ticker']}{' (S)' if r.get('side') == 'short' else ''} "
                      f"{float(r['price']):,.2f} vs trend {float(r['tv_trend']):,.2f}{d}")
        elif z == "trade_break":
            rb.append(r["ticker"] + (" (S)" if r.get("side") == "short" else ""))
    if not tb and not rb:
        return None
    lines = ["Held names vs their TradingView lines at the close."]
    if tb:
        lines.append("🛑 Below TREND (stop per rulebook):\n" + "\n".join(f"  {x}" for x in tb))
    if rb:
        lines.append("⚠️ Below TRADE (stop buying / minimum): " + ", ".join(rb))
    return "\n".join(lines)


# ─────────────────────────── data assembly ───────────────────────────

def _level_rows() -> list:
    """Held tickers (raw side — the held instrument's own lines, so inverse
    ETFs like TBT are judged in their own frame) + live price and TV lines
    straight from v_screener. Names without a TV trend line are dropped."""
    from tools.book_direction import book_sides
    import db_pg
    sides = book_sides()
    held = {t: (v.get("raw_side") or v.get("side")) for t, v in sides.items()
            if v.get("side") in ("long", "short")}
    if not held:
        return []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, price, tv_trade, tv_trend, tv_basis, tv_bar_date "
                    "FROM v_screener WHERE ticker = ANY(%s)", (list(held),))
        got = cur.fetchall()
    rows = []
    for t, price, trade, trend, basis, bd in got:
        if trend is None or price is None:
            continue
        rows.append({"ticker": t, "side": held[t], "price": float(price),
                     "tv_trade": float(trade) if trade is not None else None,
                     "tv_trend": float(trend), "tv_basis": basis,
                     "tv_bar_date": str(bd) if bd else None})
    return rows

def _book_rows(*, include_dark: bool = False) -> list:
    """Holdings with adjusted trend (screener's own COALESCE + BTCQ + wrapper
    inversion), current rp, and 5-day rp extremes from mfr_snapshots.

    include_dark=False (the default, and every alert path): held names with
    no MFR coverage are DROPPED — nothing to judge, nothing to alert on.
    include_dark=True (listings: BOOK RP, MFR COVERAGE): those names are
    emitted with rp_now/rp_5d_max/rp_5d_min None and "dark": True, because a
    book listing that hides what it cannot range answers the wrong question."""
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
    # THE resolver (2026-08-26): published MFR position first, derived band
    # second, shadow third, wrapper fourth — same order every surface uses.
    # Alert THRESHOLDS are unchanged; the VALUES they see are now the
    # vendor's own where it publishes one.
    from tools.rp_resolve import apply_rp_resolution, published_map
    apply_rp_resolution(slice_)
    by_t = {r["ticker"]: r for r in slice_}
    # wrapper tier: a held name with NO rp of its own but a linked underlying
    # takes the underlying's published rp, inverted when the link is inverse.
    try:
        from tools.wrapper_links import get_links
        links = get_links()
    except Exception:
        links = {}
    wrap_under = {w: l for w, l in links.items()
                  if w in set(held)
                  and (by_t.get(w) is None
                       or by_t[w].get("range_pos") is None)}
    if wrap_under:
        upub = published_map({l["underlying"] for l in wrap_under.values()})
        for w, l in wrap_under.items():
            ps = upub.get((l.get("underlying") or "").upper(), (None,))[0]
            if ps is None or w not in by_t:
                continue
            rp = (1.0 - float(ps)) if l.get("inverse") else float(ps)
            by_t[w]["range_pos"] = rp
            by_t[w]["rp_source"] = "wrapper"

    ext: dict = {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        # 083: the published position first, per-day, so the 5d band is
        # consistent with the tier-1 rp_now instead of mixing sources.
        cur.execute(
            """SELECT ticker,
                      max(COALESCE(mfr_pos_short,
                          (price - range_low)
                          / NULLIF(range_high - range_low, 0))),
                      min(COALESCE(mfr_pos_short,
                          (price - range_low)
                          / NULLIF(range_high - range_low, 0)))
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
            # no MFR coverage — DARK. Nothing to judge for ALERTS (default);
            # listings ask for these rows explicitly via include_dark.
            if include_dark:
                rows.append({"ticker": t,
                             "side": sides[t].get("raw_side") or sides[t]["side"],
                             "trend_dir": None, "rp_now": None,
                             "rp_5d_max": None, "rp_5d_min": None,
                             "wrap": None, "dark": True})
            continue
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
                     "rp_lt": (float(r["rp_lt"])
                               if r.get("rp_lt") is not None else None),
                     "rp_source": r.get("rp_source"),
                     "price": (float(r["price"])
                               if r.get("price") is not None else None),
                     "range_low": (float(r["range_low"])
                                   if r.get("range_low") is not None else None),
                     "range_high": (float(r["range_high"])
                                    if r.get("range_high") is not None
                                    else None),
                     "range_asof": r.get("range_asof"),
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

    # TRADE/TREND line crosses — independent assembly, so a failure here
    # never blocks dip/rip/flip (and vice versa).
    level_rows, zones = [], None
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    now_et = _dt.now(ZoneInfo("America/New_York"))
    at_close = (now_et.hour, now_et.minute) >= (16, 0)
    try:
        level_rows = _level_rows()
        lv_alerts, zones = detect_levels(level_rows, _load_state(_LEVEL_KEY),
                                         at_close=at_close)
        alerts += lv_alerts
        summary["levels_tracked"] = len(level_rows)
        summary["below_trend"] = sorted(t for t, z in zones.items() if z == "trend_break")
        if at_close:
            msg = close_summary(level_rows)
            if msg:
                alerts.append({"type": "close_summary", "ticker": "_BOOK",
                               "line": msg})
    except Exception as e:
        log.exception("book_alerts: level assembly failed")
        summary["level_error"] = str(e)
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
                    title = ("📗 BOOK vs TRADE/TREND — CLOSE"
                             if a["type"] == "close_summary"
                             else f"📗 BOOK {a['type'].upper()} {a['ticker']}")
                    send_telegram(title, a["line"])
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
        if zones is not None:
            _save_state(_LEVEL_KEY, zones)
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
