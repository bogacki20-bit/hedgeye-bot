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
    """Fetch fronts, compute cracks, upsert today. None on feed failure."""
    try:
        import yfinance as yf
        px = {}
        for sym, key in (("CL=F", "cl1"), ("HO=F", "ho1"), ("RB=F", "rb1")):
            h = yf.Ticker(sym).history(period="5d")
            if h is None or not len(h):
                log.warning("energy_complex: no bars for %s", sym)
                return None
            px[key] = float(h["Close"].iloc[-1])
    except Exception as e:  # noqa: BLE001
        log.warning("energy_complex fetch failed: %s", e)
        return None
    cl, ho, rb = px["cl1"], px["ho1"], px["rb1"]
    row = {"cl1": cl, "ho1": ho, "rb1": rb,
           "diesel_crack": ho * GAL_PER_BBL - cl,
           "gas_crack": rb * GAL_PER_BBL - cl,
           "crack_321": (2 * rb * GAL_PER_BBL + ho * GAL_PER_BBL - 3 * cl) / 3}
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO energy_cracks_daily
                (as_of, cl1, ho1, rb1, diesel_crack, gas_crack, crack_321)
            VALUES (CURRENT_DATE, %(cl1)s, %(ho1)s, %(rb1)s,
                    %(diesel_crack)s, %(gas_crack)s, %(crack_321)s)
            ON CONFLICT (as_of) DO UPDATE SET
                cl1=EXCLUDED.cl1, ho1=EXCLUDED.ho1, rb1=EXCLUDED.rb1,
                diesel_crack=EXCLUDED.diesel_crack,
                gas_crack=EXCLUDED.gas_crack, crack_321=EXCLUDED.crack_321,
                fetched_at=now()""", row)
        conn.commit()
    return row


def _delta(cur_v, hist, key, back):
    if cur_v is None or len(hist) <= back or hist[back][1][key] is None:
        return ""
    d = float(cur_v) - float(hist[back][1][key])
    return f" ({d:+.1f})" if abs(d) >= 0.05 else " (flat)"


def build_block() -> list[str]:
    """Display lines for MARKET / EOD. Refreshes today's row first."""
    fetch_and_store()
    raw = _rows("""
        SELECT as_of, cl1, ho1, rb1, diesel_crack, gas_crack, crack_321
        FROM energy_cracks_daily ORDER BY as_of DESC LIMIT 6""")
    if not raw:
        return ["⛽ ENERGY COMPLEX: feed unavailable (no stored rows)"]
    hist = [(r[0], {"cl1": r[1], "ho1": r[2], "rb1": r[3], "dc": r[4],
                    "gc": r[5], "c321": r[6]}) for r in raw]
    d0, v = hist[0]
    stale = (dt.date.today() - d0).days
    tag = f" ⚠{stale}d old" if stale > 1 else ""

    def line(label, key, unit, prec):
        s = f"  {label:<15}{float(v[key]):.{prec}f} {unit}"
        d1 = _delta(v[key], hist, key, 1)
        d5 = _delta(v[key], hist, key, 5)
        return s + (f" Δ1d{d1}" if d1 else "") + (f" Δ5d{d5}" if d5 else "")

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
