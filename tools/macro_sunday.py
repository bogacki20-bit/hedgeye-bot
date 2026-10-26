"""macro_sunday.py — "Macro Monday" game plan, run on Sunday.

Keith-style global sweep before the week: for each country, its currency,
its bonds (where the bot has them) and its equity index — trend, range
position, 1w/1m — and a verdict:
    SHORT BOUNCE  bearish trend, high in range (sell rips)
    BUY DIP       bullish trend, low in range (buy red)
    long / short  trend without an edge in range yet

    py -m tools.macro_sunday          # HUD block + full board
    py -m tools.macro_sunday --hud

Data: v_screener (trend_dir, range_pos — ETFs), tv_levels (futures/FX/index
closes vs MFR trend band + range), Yahoo closes for returns. Read-only.
"""
from __future__ import annotations

import argparse
import sys
import warnings

warnings.filterwarnings("ignore")

# country: [(role, symbol, source)]  source: 'etf' (v_screener+yahoo) | 'tv' (tv_levels)
BOARD = {
    "US":          [("fx", "UUP", "etf"), ("10y yield", "UST10Y", "tv"), ("eq", "SPY", "etf")],
    "Eurozone":    [("fx", "FXE", "etf"), ("Bund (px)", "FGBL_F", "tv"), ("eq", "VGK", "etf")],
    "Germany":     [("10y yield", "DE10Y", "tv"), ("eq", "EWG", "etf")],
    "France":      [("OAT (px)", "FOAT_F", "tv"), ("eq", "EWQ", "etf")],
    "Italy":       [("BTP (px)", "FBTP_F", "tv")],
    "UK":          [("fx", "FXB", "etf"), ("10y yield", "GB10Y", "tv"), ("eq", "EWU", "etf")],
    "Japan":       [("fx", "FXY", "etf"), ("10y yield", "JP10Y", "tv"), ("eq", "EWJ", "etf")],
    "Canada":      [("fx", "FXC", "etf"), ("10y yield", "CA10Y", "tv"), ("eq", "EWC", "etf")],
    "Switzerland": [("fx", "FXF", "etf"), ("eq", "EWL", "etf")],
    "China":       [("USD/CNY", "USD/CNY", "tv"), ("eq", "FXI", "etf"), ("internet", "KWEB", "etf")],
    "S. Korea":    [("eq", "EWY", "etf")],
    "India":       [("eq", "INDA", "etf")],
    "Australia":   [("eq", "EWA", "etf")],
    "Brazil":      [("eq", "EWZ", "etf")],
    "Mexico":      [("eq", "EWW", "etf")],
    "Global bonds": [("ex-US agg", "BNDX", "etf"), ("EM debt", "EMB", "etf")],
}


def _etf_signals(tickers):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT ticker, trend_dir, range_pos FROM v_screener WHERE ticker = ANY(%s)",
                    (list(tickers),))
        return {t: (td, float(rp) if rp is not None else None) for t, td, rp in cur.fetchall()}


def _tv_series(tickers):
    """Per symbol: list of (date, close, mfr_lower, mfr_upper, range_low, range_high)."""
    import db_pg
    out = {}
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""SELECT ticker, bar_date, bar_close, mfr_trend_lower, mfr_trend_upper,
                              range_low, range_high
                       FROM tv_levels WHERE ticker = ANY(%s) AND bar_close IS NOT NULL
                       ORDER BY bar_date""", (list(tickers),))
        for t, d, *v in cur.fetchall():
            out.setdefault(t, []).append((d, *[float(x) if x is not None else None for x in v]))
    return out


def _verdict(td, rp):
    if td == "BULLISH":
        return "BUY DIP" if rp is not None and rp <= 0.40 else "long"
    if td == "BEARISH":
        return "SHORT BOUNCE" if rp is not None and rp >= 0.60 else "short"
    return "-"


def build():
    import yfinance as yf
    etfs = {s for rows in BOARD.values() for _, s, src in rows if src == "etf"}
    tvs = {s for rows in BOARD.values() for _, s, src in rows if src == "tv"}
    sig = _etf_signals(etfs)
    ser = _tv_series(tvs)
    px = yf.download(sorted(etfs), period="3mo", progress=False, auto_adjust=True)["Close"].ffill()

    rows = []
    for country, items in BOARD.items():
        for role, sym, src in items:
            r1w = r1m = td = rp = last = None
            if src == "etf" and sym in px:
                s = px[sym].dropna()
                if len(s) > 22:
                    last = s.iloc[-1]
                    r1w, r1m = 100 * (s.iloc[-1] / s.iloc[-6] - 1), 100 * (s.iloc[-1] / s.iloc[-22] - 1)
                td, rp = sig.get(sym, (None, None))
            elif src == "tv" and ser.get(sym):
                s = ser[sym]
                d, last, lo_b, up_b, rlo, rhi = s[-1]

                def back(n):
                    return s[-1 - n][1] if len(s) > n else None
                b5, b21 = back(5), back(21)
                r1w = 100 * (last / b5 - 1) if b5 else None
                r1m = 100 * (last / b21 - 1) if b21 else None
                if lo_b and up_b:
                    mid = (lo_b + up_b) / 2
                    td = "BULLISH" if last > up_b else "BEARISH" if last < lo_b else (
                        "BULLISH" if last >= mid else "BEARISH")
                if rlo and rhi and rhi > rlo:
                    rp = (last - rlo) / (rhi - rlo)
            rows.append(dict(country=country, role=role, sym=sym, last=last, r1w=r1w, r1m=r1m,
                             td=td or "-", rp=rp, verdict=_verdict(td, rp)))
    return rows


def hud(rows):
    pick = lambda v: [r for r in rows if r["verdict"] == v]
    fx_up = [r for r in rows if r["role"] == "fx" and r["td"] == "BULLISH" and r["sym"] != "UUP"]
    j = lambda xs, n=4: " · ".join(f"{x['sym']}" for x in xs[:n]) or "none"
    short_b = sorted(pick("SHORT BOUNCE"), key=lambda r: -(r["rp"] or 0))
    buy_d = sorted(pick("BUY DIP"), key=lambda r: (r["rp"] or 1))
    usd = next((r for r in rows if r["sym"] == "UUP"), None)
    ust = next((r for r in rows if r["sym"] == "UST10Y"), None)
    return [f"SHORT BOUNCES: {j(short_b)}",
            f"BUY DIPS: {j(buy_d)}",
            f"FX BULLISH vs USD: {j(fx_up)}",
            f"USD {usd['td'].lower() if usd else '?'} · 10Y {ust['last']:.2f} {ust['td'].lower()}" if ust and ust["last"] else "USD/10Y n/a"]


def table(rows):
    f = lambda v: f"{v:+6.1f}" if v is not None else "     -"
    out = [f"{'country':12} {'role':10} {'sym':7} {'last':>9} {'1w':>6} {'1m':>6} {'trend':>8} {'range':>5}  verdict"]
    for r in rows:
        rp = f"{r['rp']:.2f}" if r["rp"] is not None else "  -"
        last = f"{r['last']:9.2f}" if r["last"] is not None else "        -"
        out.append(f"{r['country']:12} {r['role']:10} {r['sym']:7} {last} {f(r['r1w'])} {f(r['r1m'])} "
                   f"{r['td']:>8} {rp:>5}  {r['verdict']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hud", action="store_true")
    a = ap.parse_args()
    rows = build()
    print("\n".join(hud(rows)))
    if not a.hud:
        print("\nbond futures are PRICE (down = yields up). Foreign 10y yields + USD/CNY fill in after the next TradingView run.\n")
        print("\n".join(table(rows)))


if __name__ == "__main__":
    sys.exit(main())
