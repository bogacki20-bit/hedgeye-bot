"""energy_complex.py — diesel / CL1 / crack spreads for MARKET and the EOD
report (operator 9/20: 'fairly important for our book' — the crack spread
is the refiner sleeve's earnings driver: VLO, MPC, PSX, CVI, CRAK).

fetch_and_store()  CL=F / HO=F / RB=F front months via yfinance, computes
                   diesel crack (HO*42-CL), gasoline crack (RB*42-CL) and
                   the 3-2-1; upserts today's row in energy_cracks_daily.
build_block()      display lines with Δ1d and Δ5d from stored history —
                   point-in-time, no lookahead. Fetches first when today's
                   row is missing, and says so when the feed is down
                   rather than printing stale numbers unmarked.
"""

from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger(__name__)

GAL_PER_BBL = 42.0


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def fetch_and_store() -> dict | None:
    """Fetch fronts' daily bars, upsert EVERY bar in the window keyed by the
    bar's own date. None on feed failure.

    desk 10/5 #6: rows used to be stamped CURRENT_DATE with whatever print
    the fetch saw — weekend rows, a missing 10/2, intraday partials mixed
    with closes (9/29 stored HO 4.403 vs a 4.898 settle), so the gasoline
    crack Δ1d read -8 against the wrong day. Prior sessions now carry their
    closes; only the live session's row is a partial."""
    try:
        import yfinance as yf
        bars: dict = {}
        for sym, key in (("CL=F", "cl1"), ("HO=F", "ho1"), ("RB=F", "rb1")):
            h = yf.Ticker(sym).history(period="10d")
            if h is None or not len(h):
                log.warning("energy_complex: no bars for %s", sym)
                return None
            for ts, v in h["Close"].dropna().items():
                bars.setdefault(ts.date(), {})[key] = float(v)
    except Exception as e:  # noqa: BLE001
        log.warning("energy_complex fetch failed: %s", e)
        return None
    rows = []
    for d in sorted(bars):
        b = bars[d]
        if len(b) < 3:
            continue
        cl, ho, rb = b["cl1"], b["ho1"], b["rb1"]
        rows.append({"as_of": d, "cl1": cl, "ho1": ho, "rb1": rb,
                     "diesel_crack": ho * GAL_PER_BBL - cl,
                     "gas_crack": rb * GAL_PER_BBL - cl,
                     "crack_321": (2 * rb * GAL_PER_BBL + ho * GAL_PER_BBL - 3 * cl) / 3})
    if not rows:
        return None
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for row in rows:
            cur.execute("""
                INSERT INTO energy_cracks_daily
                    (as_of, cl1, ho1, rb1, diesel_crack, gas_crack, crack_321)
                VALUES (%(as_of)s, %(cl1)s, %(ho1)s, %(rb1)s,
                        %(diesel_crack)s, %(gas_crack)s, %(crack_321)s)
                ON CONFLICT (as_of) DO UPDATE SET
                    cl1=EXCLUDED.cl1, ho1=EXCLUDED.ho1, rb1=EXCLUDED.rb1,
                    diesel_crack=EXCLUDED.diesel_crack,
                    gas_crack=EXCLUDED.gas_crack, crack_321=EXCLUDED.crack_321,
                    fetched_at=now()""", row)
        conn.commit()
    return rows[-1]


def _delta(cur_v, hist, key, back, prec=1):
    if cur_v is None or len(hist) <= back or hist[back][1][key] is None:
        return ""
    d = float(cur_v) - float(hist[back][1][key])
    # HO1 is $/gal: a 4c 5-day move printed "flat" at one decimal
    return f" ({d:+.{prec}f})" if abs(d) >= 0.5 * 10 ** -prec else " (flat)"


def build_block() -> list[str]:
    """Display lines for MARKET / EOD. Refreshes today's row first."""
    fetch_and_store()
    raw = _rows("""
        SELECT as_of, cl1, ho1, rb1, diesel_crack, gas_crack, crack_321
        FROM energy_cracks_daily ORDER BY as_of DESC LIMIT 12""")
    from tools.trading_calendar import is_trading_day
    raw = [r for r in raw if is_trading_day(r[0])][:6]
    if not raw:
        return ["⛽ ENERGY COMPLEX: feed unavailable (no stored rows)"]
    hist = [(r[0], {"cl1": r[1], "ho1": r[2], "rb1": r[3], "dc": r[4],
                    "gc": r[5], "c321": r[6]}) for r in raw]
    d0, v = hist[0]
    stale = (dt.date.today() - d0).days
    tag = f" ⚠{stale}d old" if stale > 1 else ""

    # ranges on the fronts (operator 9/21): Hedgeye WTIC risk range for
    # CL1 (senior source, daily email), MFR HO_F band for diesel
    def _rp(px, lo, hi):
        return max(0.0, min(1.0, (px - lo) / (hi - lo))) if hi > lo else None

    rr = {}
    try:
        r = _rows("SELECT buy_trade, sell_trade, trend FROM hedgeye_risk_ranges "
                  "WHERE ticker='WTIC' AND signal_date >= CURRENT_DATE - 7 "
                  "ORDER BY signal_date DESC LIMIT 1")
        if r and v["cl1"] is not None:
            lo, hi, tr = float(r[0][0]), float(r[0][1]), r[0][2]
            rp = _rp(float(v["cl1"]), lo, hi)
            rr["cl1"] = f"  {tr or ''} rp={rp:.2f} [{lo:g}-{hi:g}] hdg"
        r = _rows("SELECT range_low, range_high, trend_signal FROM mfr_snapshots "
                  "WHERE ticker='HO_F' AND snapshot_date >= CURRENT_DATE - 5 "
                  "AND range_low IS NOT NULL ORDER BY snapshot_date DESC LIMIT 1")
        if r and v["ho1"] is not None:
            lo, hi, tr = float(r[0][0]), float(r[0][1]), (r[0][2] or "")
            rp = _rp(float(v["ho1"]), lo, hi)
            tr = {"trendBullish": "BULLISH", "trendBearish": "BEARISH",
                  "trendNeutral": "NEUT"}.get(tr, tr)
            rr["ho1"] = f"  {tr} rp={rp:.2f} [{lo:g}-{hi:g}] mfr"
    except Exception as e:  # noqa: BLE001
        log.warning("energy range lookup failed: %s", e)

    def line(label, key, unit, prec):
        s = f"  {label:<15}{float(v[key]):.{prec}f} {unit}"
        d1 = _delta(v[key], hist, key, 1, prec)
        d5 = _delta(v[key], hist, key, 5, prec)
        return (s + (f" Δ1d{d1}" if d1 else "") + (f" Δ5d{d5}" if d5 else "")
                + rr.get(key, ""))

    return [f"⛽ ENERGY COMPLEX ({d0}{tag}) — the refiner sleeve's driver "
            f"(VLO/MPC/PSX/CVI/CRAK):",
            line("CL1 (WTI)", "cl1", "$/bbl", 2),
            line("Diesel (HO1)", "ho1", "$/gal", 3),
            line("Diesel crack", "dc", "$/bbl", 1),
            line("Gasoline crack", "gc", "$/bbl", 1),
            line("3-2-1 crack", "c321", "$/bbl", 1)]


if __name__ == "__main__":
    import sys
    for _s in (sys.stdout,):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    import db_pg
    db_pg._load_dotenv_fallback()
    print("\n".join(build_block()))
