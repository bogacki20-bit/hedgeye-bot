"""SPX 1-month vs 3-month realized vol: exact series, crosses, and a projection.

Tier1 Alpha charts 1m vs 3m realized vol (MSR p4), but a chart line can't date
a cross. This computes it from ^GSPC closes (annualized close-to-close stdev,
21 / 63 sessions), stores it in spx_realized_vol, flags crosses, and projects
forward: if SPX goes flat (0% days), which big days roll out of each window
and when does 1m cross 3m. That's the same logic as T1A's drop-off table.

    py -m tools.rv_cross               # refresh the table (1y history), print status
    py -m tools.rv_cross --status      # print status from the table
"""
from __future__ import annotations

import argparse
import math

W1, W3, W10 = 21, 63, 10
ANN = math.sqrt(252)
# Aug 2026 flipped 6x on gaps of a few hundredths — a cross only counts once
# the 1m-3m gap clears this band on the other side (hysteresis).
CROSS_BAND = 0.25


def _rv(rets: list[float], n: int) -> float | None:
    if len(rets) < n:
        return None
    w = rets[-n:]
    m = sum(w) / n
    return math.sqrt(sum((r - m) ** 2 for r in w) / (n - 1)) * ANN * 100


def compute(closes: list[tuple]) -> list[dict]:
    """closes: [(date, close)] ascending -> rows with rv + cross flags."""
    rets, rows, state = [], [], None          # state: 'above' | 'below'
    for i, (d, c) in enumerate(closes):
        if i:
            rets.append(math.log(c / closes[i - 1][1]))
        r1, r3 = _rv(rets, W1), _rv(rets, W3)
        spread = (r1 - r3) if r1 is not None and r3 is not None else None
        crossed = None
        if spread is not None:
            if state is None:              # seed the side from the first gap's sign,
                state = "above" if spread > 0 else "below"   # without firing
            new = ("above" if spread > CROSS_BAND else
                   "below" if spread < -CROSS_BAND else state)
            if new != state:
                crossed = "up" if new == "above" else "down"
            state = new
        rows.append({"trade_date": d, "spx_close": c, "rv_10d": _rv(rets, W10),
                     "rv_1m": r1, "rv_3m": r3, "spread_1m_3m": spread,
                     "crossed": crossed})
    return rows


def project_flat(closes: list[tuple], days: int = 15) -> list[dict]:
    """Assume SPX is unchanged each day ahead: rv path as old days roll off."""
    rets = [math.log(closes[i][1] / closes[i - 1][1]) for i in range(1, len(closes))]
    out = []
    for k in range(1, days + 1):
        r = rets + [0.0] * k
        r1, r3 = _rv(r, W1), _rv(r, W3)
        out.append({"t": k, "rv_1m": r1, "rv_3m": r3,
                    "spread": None if r1 is None or r3 is None else r1 - r3})
    return out


def fetch_closes(period: str = "1y") -> list[tuple]:
    import yfinance as yf
    s = yf.Ticker("^GSPC").history(period=period, auto_adjust=True)["Close"].dropna()
    return [(ix.date(), float(v)) for ix, v in s.items()]


def refresh() -> list[dict]:
    import db_pg
    closes = fetch_closes()
    rows = compute(closes)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for r in rows:
            if r["rv_3m"] is None:
                continue
            cur.execute(
                "INSERT INTO spx_realized_vol (trade_date, spx_close, rv_10d, rv_1m, rv_3m, "
                "spread_1m_3m, crossed) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (trade_date) DO UPDATE SET spx_close=EXCLUDED.spx_close, "
                "rv_10d=EXCLUDED.rv_10d, rv_1m=EXCLUDED.rv_1m, rv_3m=EXCLUDED.rv_3m, "
                "spread_1m_3m=EXCLUDED.spread_1m_3m, crossed=EXCLUDED.crossed, computed_at=now()",
                [r[k] for k in ("trade_date", "spx_close", "rv_10d", "rv_1m", "rv_3m",
                                "spread_1m_3m", "crossed")])
        conn.commit()
    return closes


def status_lines(closes: list[tuple] | None = None) -> list[str]:
    """Current 1m/3m, last cross, and the flat-market projection."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT trade_date, rv_10d, rv_1m, rv_3m, spread_1m_3m FROM spx_realized_vol "
                    "ORDER BY trade_date DESC LIMIT 1")
        last = cur.fetchone()
        cur.execute("SELECT trade_date, crossed FROM spx_realized_vol WHERE crossed IS NOT NULL "
                    "ORDER BY trade_date DESC LIMIT 1")
        xr = cur.fetchone()
        if closes is None:                 # projection from stored closes — no network
            cur.execute("SELECT trade_date, spx_close FROM spx_realized_vol "
                        "ORDER BY trade_date DESC LIMIT 130")
            closes = [(d_, float(c_)) for d_, c_ in reversed(cur.fetchall())] or None
    if not last:
        return ["📉 SPX realized vol: no data (run py -m tools.rv_cross)"]
    d, r10, r1, r3, sp = last
    state = "1m ABOVE 3m (vol expanding)" if sp > 0 else "1m BELOW 3m (vol compressing)"
    lines = [f"📉 SPX rVol {d:%m/%d}: 10d {float(r10):.1f} · 1m {float(r1):.2f} · 3m {float(r3):.2f}"
             f" · {state}, gap {float(sp):+.2f}"]
    if xr:
        lines.append(f"  last cross: 1m crossed {'above' if xr[1] == 'up' else 'below'} 3m on {xr[0]:%m/%d}")
    if closes:
        proj = project_flat(closes)
        want = (lambda s: s < -CROSS_BAND) if float(sp) > 0 else (lambda s: s > CROSS_BAND)
        flip = next((p for p in proj if p["spread"] is not None and want(p["spread"])), None)
        if flip:
            lines.append(f"  flat-market projection: 1m crosses 3m in ~{flip['t']} sessions "
                         f"(1m {flip['rv_1m']:.2f} vs 3m {flip['rv_3m']:.2f})")
        else:
            p5 = proj[4]
            lines.append(f"  flat-market projection: no cross in 15 sessions "
                         f"(T+5: 1m {p5['rv_1m']:.2f} vs 3m {p5['rv_3m']:.2f})")
    return lines


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    import db_pg
    db_pg._load_dotenv_fallback()
    closes = None if a.status else refresh()
    print("\n".join(status_lines(closes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
