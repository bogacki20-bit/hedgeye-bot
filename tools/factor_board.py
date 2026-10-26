"""factor_board.py — where to rotate: factors + sectors ranked by relative strength.

For each factor/sector ETF: 1w / 1m / 3m return, return RELATIVE to SPY,
the bot's trend_dir and range position (v_screener, where covered), and a
simple lead/lag score. Prints a 4-line HUD block first, then the table.

    py -m tools.factor_board            # factors + sectors
    py -m tools.factor_board --hud      # HUD block only

Score = 1m relative return (pts) + 0.5 * 3m relative + trend bonus
(+2 BULLISH, -2 BEARISH). Read-only: Yahoo closes + v_screener.
"""
from __future__ import annotations

import argparse
import sys
import warnings

warnings.filterwarnings("ignore")

FACTORS = {
    "MTUM": "Momentum", "JOET": "Quality momentum", "SPHB": "High beta",
    "SPLV": "Low vol", "USMV": "Min vol", "QUAL": "Quality",
    "IVE": "Value", "IVW": "Growth", "VYM": "High dividend", "NOBL": "Div aristocrats",
    "LVHI": "Intl high div", "RSP": "Equal weight", "IWM": "Small caps",
    "BTAL": "Anti-beta (L/S)",
}
SECTORS = {
    "XLV": "Healthcare", "XLP": "Staples", "XLE": "Energy", "XLU": "Utilities",
    "XLF": "Financials", "XLI": "Industrials", "XLK": "Tech", "XLY": "Discretionary",
    "XLC": "Comm services", "XLB": "Materials", "XLRE": "Real estate",
    "SMH": "Semis", "IGV": "Software", "ITB": "Homebuilders", "CRAK": "Refiners",
    "XOP": "E&P producers",
}


def _screener(tickers):
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT ticker, trend_dir, range_pos FROM v_screener WHERE ticker = ANY(%s)",
                        (list(tickers),))
            return {t: (td, float(rp) if rp is not None else None) for t, td, rp in cur.fetchall()}
    except Exception:
        return {}


def build():
    import yfinance as yf
    names = {**FACTORS, **SECTORS}
    tick = list(names) + ["SPY"]
    px = yf.download(tick, period="4mo", progress=False, auto_adjust=True)["Close"].ffill()
    sc = _screener(names)

    def ret(t, n):
        s = px[t].dropna()
        return 100 * (s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None

    spy = {n: ret("SPY", n) for n in (5, 21, 63)}
    rows = []
    for t, label in names.items():
        if t not in px or px[t].dropna().empty:
            continue
        r = {n: ret(t, n) for n in (5, 21, 63)}
        rel = {n: (r[n] - spy[n]) if r[n] is not None and spy[n] is not None else None for n in r}
        td, rp = sc.get(t, (None, None))
        score = (rel[21] or 0) + 0.5 * (rel[63] or 0) + {"BULLISH": 2, "BEARISH": -2}.get(td or "", 0)
        rows.append(dict(t=t, label=label, kind="F" if t in FACTORS else "S", r=r, rel=rel,
                         td=td or "-", rp=rp, score=score))
    rows.sort(key=lambda x: -x["score"])
    return rows, px.index[-1].date()


def hud(rows):
    f = [r for r in rows if r["kind"] == "F"]
    s = [r for r in rows if r["kind"] == "S"]
    j = lambda xs: " · ".join(x["t"] for x in xs)
    return [f"FACTORS LEAD: {j(f[:3])}", f"FACTORS LAG: {j(f[-3:][::-1])}",
            f"SECTORS IN: {j(s[:3])}", f"SECTORS OUT: {j(s[-3:][::-1])}"]


def table(rows):
    out = [f"{'':5} {'name':17} {'1w':>6} {'1m':>6} {'3m':>6} {'1m vs SPY':>9} {'trend':>8} {'range':>5} {'score':>6}"]
    for kind, title in (("F", "— FACTORS —"), ("S", "— SECTORS —")):
        out.append(title)
        for r in (x for x in rows if x["kind"] == kind):
            f = lambda v: f"{v:+6.1f}" if v is not None else "     -"
            rp = f"{r['rp']:.2f}" if r["rp"] is not None else "  -"
            out.append(f"{r['t']:5} {r['label'][:17]:17} {f(r['r'][5])} {f(r['r'][21])} {f(r['r'][63])} "
                       f"{f(r['rel'][21]):>9} {r['td']:>8} {rp:>5} {r['score']:+6.1f}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hud", action="store_true")
    a = ap.parse_args()
    rows, asof = build()
    print("\n".join(hud(rows)))
    if not a.hud:
        print(f"\nas of {asof} close · score = 1m rel + ½·3m rel ± trend\n")
        print("\n".join(table(rows)))


if __name__ == "__main__":
    sys.exit(main())
