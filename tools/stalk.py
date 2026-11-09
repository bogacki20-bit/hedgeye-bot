"""Live stalk list (Kris, 11/5/2026).

    LONG   bond proxies in a Bullish Formation: buy them on down days, near Trade
    SHORT  Signal Strength, ETF Pro and AI-bubble shorts in a Bearish Formation: short strength

    py -m tools.stalk

Long ranks Signal Strength longs first, then the closest to Trade. Short shows how the name sits
against the rule card's entry: below Trade, range position 0.65+, green day, 3+ day bounce.
Earnings within 2 days block a new position, so each name shows its next date.
"""
from __future__ import annotations

import datetime as dt
import sys
import warnings

MAX_ABOVE_TRADE = 0.06   # further past Trade than this isn't a dip-buy
EARN_GATE = 2


def bounce_days(closes: list) -> int:
    """Pure: how many sessions in a row the close rose, ending at the last one."""
    n = 0
    for a, b in zip(reversed(closes[:-1]), reversed(closes[1:])):
        if b <= a:
            break
        n += 1
    return n


def short_entry(px: float, trade: float, rp, up_today: bool, bounce: int) -> list[str]:
    """Pure: which parts of the rule card's short entry are still missing."""
    miss = []
    if px >= trade:
        miss.append("above Trade")
    if rp is None or rp < 0.65:
        miss.append("rp low")
    if not up_today:
        miss.append("red day")
    if bounce < 3:
        miss.append("no 3-day bounce")
    return miss


def _earnings(t: str, today: dt.date) -> str:
    import yfinance as yf
    try:
        e = yf.Ticker(t).calendar.get("Earnings Date")
    except Exception:  # noqa: BLE001
        return "earn ?"
    if not e:
        return "earn ?"
    d = e[0]
    days = (d - today).days
    if 0 <= days <= EARN_GATE:
        return f"EARNINGS {d:%m/%d}: blocked"
    return f"earn {d:%m/%d}"


def run() -> int:
    import db_pg
    import yfinance as yf
    from tools.bond_proxies import measure
    from tools.formations import formation
    from tools.level_lines import PROXY, lookup
    from tools.quotes import yahoo_symbol
    warnings.filterwarnings("ignore")
    db_pg._load_dotenv_fallback()
    today = dt.date.today()
    longs = [r for r in measure() if r["form"].startswith("bullish") and not r["held"]]
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM book_positions WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)")
        held = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT ticker, 'SS' FROM ss_roster_current WHERE side='short' UNION "
                    "SELECT ticker, 'ETF' FROM hedgeye_etf_pro_ranges WHERE bias='short' AND week_of=(SELECT max(week_of) FROM hedgeye_etf_pro_ranges) UNION "
                    "SELECT ticker, 'AI' FROM ai_short_watch")
        tags: dict[str, list] = {}
        for t, k in cur.fetchall():
            tags.setdefault(t, []).append(k)
        cur.execute("SELECT ticker, range_low, range_high, trend_dir FROM v_screener WHERE ticker = ANY(%s)",
                    (list(tags) + [r["t"] for r in longs],))
        scr = {t: (l, h, d) for t, l, h, d in cur.fetchall()}
    short_names = [t for t in tags if t not in held and t not in PROXY]
    sym = {t: yahoo_symbol(t) for t in set(short_names) | {r["t"] for r in longs}}
    sym = {t: s for t, s in sym.items() if s}
    d = yf.download(sorted(set(sym.values()) | {"SPY"}), period="3mo", interval="1d", auto_adjust=False, progress=False,
                    threads=True, group_by="ticker")
    live = {}
    for t, s in sym.items():
        try:
            c = d[s]["Close"].dropna()
            live[t] = (float(c.iloc[-1]), float(c.iloc[-2]), [float(x) for x in c.values[-8:]])
        except Exception:  # noqa: BLE001
            continue
    spy = d["SPY"]["Close"].dropna()
    print(f"STALK as of {dt.datetime.now():%H:%M}. SPY {float(spy.iloc[-1]):.2f}, {100 * (spy.iloc[-1] / spy.iloc[-2] - 1):+.1f}% today.")

    print("\nLONG: bond proxies, Bullish Formation, buy on down days near Trade")
    rows = []
    for r in longs:
        if r["t"] not in live:
            continue
        px, prev, _ = live[r["t"]]
        away = px / r["tr"] - 1
        if away <= MAX_ABOVE_TRADE:
            rows.append((r["ss"] != "long", away, r, px, px / prev - 1))
    rows.sort(key=lambda x: x[:2])
    for _, away, r, px, chg in rows[:12]:
        mark = "DOWN DAY" if chg < 0 else ""
        ss = "SS long" if r["ss"] == "long" else ""
        print(f"  {r['t']:6} {px:9.2f} {100 * chg:+5.1f}%  {100 * away:+5.1f}% vs Trade {r['tr']:9.2f}  corr {r['corr']:+.2f} {ss:8}"
              f"{mark:9} {_earnings(r['t'], today)}")

    print("\nSHORT: Bearish Formation, short strength")
    lines = lookup(short_names)
    rows = []
    for t in short_names:
        if t not in live:
            continue
        px, prev, closes = live[t]
        l, h, sig = scr.get(t, (None, None, None))
        tr, tn = lines[t]["trade"][0], lines[t]["trend"][0]
        form = formation(px, tr, tn, lines[t]["tail"], sig)
        if not form.startswith("bearish"):
            continue
        rp = (px - float(l)) / (float(h) - float(l)) if l is not None and h is not None and h != l else None
        miss = short_entry(px, tr, rp, px > prev, bounce_days(closes))
        rows.append((len(miss), -(rp if rp is not None else -9), t, px, px / prev - 1, tr, rp, miss, form))
    rows.sort(key=lambda x: x[:2])
    for n, _, t, px, chg, tr, rp, miss, form in rows[:14]:
        rps = "-" if rp is None else f"{rp:.2f}"
        need = "ENTRY OK" if not miss else "wait: " + ", ".join(miss)
        print(f"  {t:6} {px:9.2f} {100 * chg:+5.1f}%  Trade {tr:9.2f} rp {rps:5} {'/'.join(tags[t]):8} {form:14} {need} | {_earnings(t, today)}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(run())
