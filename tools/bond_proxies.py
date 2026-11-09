"""Bond-proxy longs: defensives that trade like bonds (Kris, 11/5/2026).

A bond proxy here is a defensive stock or equity ETF that moves with long Treasuries and has a
low beta to the market, over the last 60 sessions. Defensive means CORE below or a staples,
utilities or health care name, because in an AI selloff plenty of other names measure low beta
for a while. Candidates are then read through the formation scan and the Trade test.

    py -m tools.bond_proxies

Correlation and beta are measured from daily returns, so they shift. They find candidates; the
rule card still decides, and earnings need checking by hand.
"""
from __future__ import annotations

import sys
import warnings

import numpy as np

from tools.formations import formation, trade_test

LOOKBACK = 60
MAX_BETA = 0.6
MIN_CORR = 0.20
DEFENSIVE_SECTORS = {"Consumer Staples", "Utilities", "Health Care"}
# Classic bond proxies, measured even when the screener doesn't carry them
CORE = """WM RSG WCN ROL KO PEP PG CL KMB GIS HSY MDLZ CPB KHC CHD CLX MO PM KDP
          DUK SO NEE AEP D XEL WEC ED AWK CMS O AMT CCI PSA WELL VICI
          JNJ MRK ABT ABBV LLY MCK CAH COR VZ T TMUS WMT COST KR MCD YUM
          CME ICE CBOE MMC AON AJG BRO PGR TRV CB VRSK
          XLP XLU XLV XLRE USMV SPLV NOBL IAK""".split()
# Not equities: the bonds themselves, cash stand-ins and hedges
NOT_PROXIES = {"TLT", "ZROZ", "IEF", "SHY", "BNDD", "BNDX", "BUXX", "CLOX", "CLOZ", "JCPB", "ILS", "LQD", "VCLT", "MBB",
               "TIP", "VTIP", "AGGH", "HYG", "BKLN", "EMB", "EMLC", "FDRXX", "BTAL", "UUP", "GLD", "AAAU"}


def beta_and_corr(asset: np.ndarray, spy: np.ndarray, bond: np.ndarray) -> tuple[float, float]:
    """Pure: (beta to the market, correlation to bonds) from aligned daily returns."""
    beta = float(np.cov(asset, spy)[0, 1] / np.var(spy, ddof=1))
    return beta, float(np.corrcoef(asset, bond)[0, 1])


def is_proxy(beta: float, corr: float) -> bool:
    """Pure: low beta to the market and moving with bonds."""
    return beta <= MAX_BETA and corr >= MIN_CORR


def measure() -> list[dict]:
    """Defensive names that pass is_proxy, read through the formation scan and the Trade test."""
    import db_pg
    import yfinance as yf
    from tools.level_lines import lookup
    from tools.quotes import yahoo_symbol
    warnings.filterwarnings("ignore")
    db_pg._load_dotenv_fallback()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, gics_sector, trend_dir FROM v_screener WHERE ticker ~ '^[A-Z]{1,5}$'")
        meta = {t: (g, d) for t, g, d in cur.fetchall()}
        cur.execute("SELECT ticker, side FROM ss_roster_current")
        ss = dict(cur.fetchall())
        cur.execute("SELECT DISTINCT symbol FROM book_positions WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)")
        held = {r[0] for r in cur.fetchall()}
    names = sorted((set(meta) | set(CORE)) - NOT_PROXIES - {"USD"})
    sym = {t: yahoo_symbol(t) for t in names}
    sym = {t: s for t, s in sym.items() if s}
    syms = sorted(set(sym.values()) | {"SPY", "TLT"})
    d = yf.download(syms, period="1y", interval="1d", auto_adjust=True, progress=False, threads=True, group_by="ticker")
    spy, bond = d["SPY"]["Close"].pct_change(), d["TLT"]["Close"].pct_change()
    found = []
    for t, s in sym.items():
        try:
            b = d[s].dropna(subset=["Close"])
            r = b["Close"].pct_change()
        except Exception:  # noqa: BLE001
            continue
        both = np.array([r, spy.reindex(r.index), bond.reindex(r.index)]).T
        both = both[~np.isnan(both).any(axis=1)][-LOOKBACK:]
        if len(both) < LOOKBACK:
            continue
        beta, corr = beta_and_corr(both[:, 0], both[:, 1], both[:, 2])
        defensive = t in CORE or (meta.get(t) or ("", ""))[0] in DEFENSIVE_SECTORS
        if defensive and is_proxy(beta, corr):
            found.append((t, beta, corr, b))
    lines = lookup([t for t, *_ in found])
    rows = []
    for t, beta, corr, b in found:
        c = b["Close"]
        px = float(c.iloc[-1])
        tr = lines[t]["trade"][0] or float(c.ewm(span=34, adjust=False).mean().iloc[-1])
        tn = lines[t]["trend"][0] or float(c.ewm(span=89, adjust=False).mean().iloc[-1])
        form = formation(px, tr, tn, lines[t]["tail"], (meta.get(t) or (None, None))[1])
        stage = trade_test(list(b["High"]), list(b["Low"]), list(c), tr, 1) if form.startswith("bullish") else ""
        rows.append({"t": t, "beta": beta, "corr": corr, "px": px, "tr": tr, "form": form, "stage": stage,
                     "ss": ss.get(t), "held": t in held, "sector": (meta.get(t) or ("", ""))[0] or ""})
    return rows


def run() -> int:
    rows = measure()
    rank = lambda r: (not r["form"].startswith("bullish"), not r["stage"], r["ss"] != "long", -r["corr"])
    print(f"BOND PROXIES: 60-day beta to SPY <= {MAX_BETA}, correlation to TLT >= {MIN_CORR}. {len(rows)} names.")
    for r in sorted(rows, key=rank):
        if not r["form"].startswith("bullish"):
            continue
        tags = " ".join(x for x in ("HELD" if r["held"] else "", f"SS {r['ss']}" if r["ss"] else "", r["stage"] and f"Trade test: {r['stage']}") if x)
        print(f"  {r['t']:6} beta {r['beta']:+.2f} corr {r['corr']:+.2f} {r['form']:14} px {r['px']:9.2f} {100 * (r['px'] / r['tr'] - 1):+5.1f}% vs Trade  {r['sector'][:18]:18} {tags}")
    others = [r["t"] for r in sorted(rows, key=rank) if not r["form"].startswith("bullish")]
    print("\nProxies not in a Bullish Formation: " + (" ".join(others) or "none"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run())
