"""Bullish / Bearish Formation scan, from Keith's process slide (Kris, 11/5/2026).

    Bullish Formation: price > TRADE > TREND > TAIL   buy the dips
    Bearish Formation: price < TRADE < TREND < TAIL   short strength, repeatedly

Lines come from tools.level_lines.lookup, so Keith's number wins. TAIL only exists for names
in his monthly Trend & Tail email: without it a name can only be '2-line', never a full
formation. A proxied name (SCHA via IWM) reads entirely on the proxy, so price and lines
are in the same units. It's a read, not an exit: the rule card's lines still decide.

The Trend signal outranks price (Kris, 11/5/2026): one rally above the lines doesn't make a
bearish-trend name a Bullish Formation (SPY on 11/4). A signal the other way reads 'mixed'.

The entry is the Trade test (Kris, 11/5/2026): in a Bullish Formation price runs up, sells
off, tests Trade, holds and rises again. In a Bearish Formation it bounces into Trade, fails
and rolls over. --setups finds names at that spot. The band and lookbacks below are the
desk's first cut, not Keith's numbers.

    py -m tools.formations            # book, then full formations not held
    py -m tools.formations AAPL IWM
    py -m tools.formations --setups   # names at the Trade test now
"""
from __future__ import annotations

import sys

TEST_BAND = 0.01         # within 1% of Trade counts as a test
TEST_LOOKBACK = 5        # sessions the test can have happened in
RUN_LOOKBACK = 20        # sessions to look back for the run that came before
MIN_RUN = 0.03           # the run reached at least 3% beyond Trade
MAX_AWAY = 0.05          # more than 5% past Trade: the entry has gone


def trade_test(highs: list, lows: list, closes: list, trade: float, side: int) -> str:
    """Pure: 'held' or 'testing' when price tested Trade from the right side and never closed through.
    Long: ran up, a recent low reached Trade, every close since stayed above it, and price is still
    near it; 'held' once it's rising. Short (side -1): the mirror, 'held' meaning the bounce failed
    and it's falling. Oldest first."""
    if trade is None or len(closes) < 2:
        return ""
    if side > 0:
        ran = max(highs[-RUN_LOOKBACK:]) >= trade * (1 + MIN_RUN)
        tested = min(lows[-TEST_LOOKBACK:]) <= trade * (1 + TEST_BAND)
        on_side, moving = min(closes[-TEST_LOOKBACK:]) > trade, closes[-1] > closes[-2]
        near = closes[-1] <= trade * (1 + MAX_AWAY)
    else:
        ran = min(lows[-RUN_LOOKBACK:]) <= trade * (1 - MIN_RUN)
        tested = max(highs[-TEST_LOOKBACK:]) >= trade * (1 - TEST_BAND)
        on_side, moving = max(closes[-TEST_LOOKBACK:]) < trade, closes[-1] < closes[-2]
        near = closes[-1] >= trade * (1 - MAX_AWAY)
    if not (ran and tested and on_side and near):
        return ""
    return "held" if moving else "testing"


def formation(price, trade, trend, tail, trend_dir: str | None = None) -> str:
    """Pure: bullish, bearish, bullish-2line, bearish-2line, or mixed. trend_dir is the stated
    Trend signal (BULLISH, BEARISH, NEUTRAL); a formation can't go against it."""
    if price is None or trade is None or trend is None:
        return "no data"
    d = (trend_dir or "").upper()
    if price > trade > trend:
        if d == "BEARISH":
            return "mixed"
        if tail is None:
            return "bullish-2line"
        return "bullish" if trend > tail else "mixed"
    if price < trade < trend:
        if d == "BULLISH":
            return "mixed"
        if tail is None:
            return "bearish-2line"
        return "bearish" if trend < tail else "mixed"
    return "mixed"


def read_position(side: int, form: str) -> str:
    """Pure: how a formation reads against a position (+1 long, -1 short)."""
    if form == "no data":
        return "no lines"
    want = "bullish" if side > 0 else "bearish"
    other = "bearish" if side > 0 else "bullish"
    if form == want:
        return "with"
    if form == f"{want}-2line":
        return "with, no Tail"
    if form.startswith(other):
        return "AGAINST"
    return "mixed"


def scan(tickers: list[str]) -> dict:
    """{ticker: {price, trade, trend, tail, form}} on the latest bot prices."""
    import db_pg
    from tools.level_lines import PROXY, TV_KEY, lookup
    src = {t: PROXY.get(t, t) for t in tickers}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker, price, trend_dir FROM v_screener WHERE ticker = ANY(%s)", (list(src.values()),))
        got = cur.fetchall()
        px = {t: float(p) for t, p, _ in got if p is not None}
        dirs = {t: d for t, _, d in got if t not in TV_KEY}      # USD's row there is the dollar's signal
        # v_screener's USD price is the dollar, not the semis ETF we hold
        cur.execute("SELECT DISTINCT ON (ticker) ticker, bar_close FROM tv_levels WHERE ticker = ANY(%s) "
                    "ORDER BY ticker, bar_date DESC", (list(TV_KEY.values()),))
        bars = {t: float(c) for t, c in cur.fetchall() if c is not None}
    for t, k in TV_KEY.items():
        if k in bars:
            px[t] = bars[k]
    lines = lookup(sorted(set(src.values())))
    out = {}
    for t, s in src.items():
        r = lines[s]
        p, tr, tn, tl = px.get(s), r["trade"][0], r["trend"][0], r["tail"]
        out[t] = {"price": p, "trade": tr, "trend": tn, "tail": tl, "trend_dir": dirs.get(s),
                  "form": formation(p, tr, tn, tl, dirs.get(s)),
                  "proxy": s if s != t else None}
    return out


def _line(t: str, r: dict, note: str = "") -> str:
    f = lambda x: "-" if x is None else f"{x:,.2f}"
    via = f" via {r['proxy']}" if r.get("proxy") else ""
    sig = (r.get("trend_dir") or "-")[:4].lower()
    return f"  {t:6} {r['form']:14} {note:14} px {f(r['price'])} tr {f(r['trade'])} tn {f(r['trend'])} tail {f(r['tail'])} sig {sig}{via}"


def setups() -> int:
    """Names in a formation that are at their Trade test, on Yahoo daily bars."""
    import warnings
    import db_pg
    import yfinance as yf
    from tools.level_lines import SYMBOL_OVERRIDE
    from tools.quotes import yahoo_symbol
    warnings.filterwarnings("ignore")
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT symbol, sum(quantity) FROM book_positions WHERE asset_class <> 'cash' AND "
                    "snapshot_date = (SELECT max(snapshot_date) FROM book_positions) GROUP BY 1")
        book = {t: (1 if float(q or 0) > 0 else -1) for t, q in cur.fetchall() if q}
        cur.execute("SELECT ticker FROM ss_roster_current UNION "
                    "SELECT ticker FROM hedgeye_etf_pro_ranges WHERE week_of = (SELECT max(week_of) FROM hedgeye_etf_pro_ranges) UNION "
                    "SELECT ticker FROM keith_levels WHERE source='trend_tail' AND as_of = (SELECT max(as_of) FROM keith_levels WHERE source='trend_tail')")
        names = sorted({r[0] for r in cur.fetchall()} | set(book))
    rows = scan(names)
    live = {t: r for t, r in rows.items() if r["form"].split("-")[0] in ("bullish", "bearish") and not r.get("proxy")}
    sym = {t: SYMBOL_OVERRIDE.get(t) or yahoo_symbol(t) for t in live}
    sym = {t: s for t, s in sym.items() if s}
    d = yf.download(sorted(set(sym.values())), period="3mo", interval="1d", auto_adjust=False, progress=False,
                    threads=True, group_by="ticker")
    found = {("bullish", "held"): [], ("bullish", "testing"): [], ("bearish", "held"): [], ("bearish", "testing"): []}
    for t, s in sym.items():
        try:
            b = (d[s] if len(set(sym.values())) > 1 else d).dropna(subset=["Close"])
        except Exception:  # noqa: BLE001
            continue
        fam = live[t]["form"].split("-")[0]
        stage = trade_test(list(b["High"]), list(b["Low"]), list(b["Close"]), live[t]["trade"], 1 if fam == "bullish" else -1)
        if stage:
            found[(fam, stage)].append(t)
    last = {t: rows[t] for t in sym}
    for (fam, stage), label in (((("bullish", "held")), "LONG: tested Trade, held, rising"),
                                ((("bullish", "testing")), "LONG: at Trade now, not turned yet"),
                                ((("bearish", "held")), "SHORT: bounced into Trade, failed, falling"),
                                ((("bearish", "testing")), "SHORT: at Trade now, not rolled yet")):
        print(f"\n{label}")
        for t in sorted(found[(fam, stage)]):
            mark = "HELD" if t in book else ""
            print(_line(t, last[t], mark))
        if not found[(fam, stage)]:
            print("  none")
    print("\nCheck earnings and the short entry rule by hand.")
    return 0


def _cli() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import db_pg
    db_pg._load_dotenv_fallback()
    if sys.argv[1:] == ["--setups"]:
        return setups()
    if len(sys.argv) > 1:
        names = [x.upper() for x in sys.argv[1:]]
        for t, r in scan(names).items():
            print(_line(t, r))
        return 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT symbol, sum(quantity) FROM book_positions WHERE asset_class <> 'cash' AND "
                    "snapshot_date = (SELECT max(snapshot_date) FROM book_positions) GROUP BY 1")
        book = {t: (1 if float(q or 0) > 0 else -1) for t, q in cur.fetchall() if q}
        cur.execute("SELECT DISTINCT ticker FROM keith_levels WHERE source='trend_tail' AND tail IS NOT NULL "
                    "AND as_of = (SELECT max(as_of) FROM keith_levels WHERE source='trend_tail')")
        tail_names = [r[0] for r in cur.fetchall()]
    rows = scan(sorted(set(book) | set(tail_names)))
    print("BOOK")
    for t, side in sorted(book.items(), key=lambda x: (read_position(x[1], rows[x[0]]["form"]) != "AGAINST", x[0])):
        print(_line(t, rows[t], f"{'L' if side > 0 else 'S'} {read_position(side, rows[t]['form'])}"))
    for label, want in (("FULL BULLISH FORMATIONS, NOT HELD (buy dips)", "bullish"),
                        ("FULL BEARISH FORMATIONS, NOT HELD (short strength)", "bearish")):
        print(f"\n{label}")
        names = [t for t, r in sorted(rows.items()) if r["form"] == want and t not in book]
        for t in names:
            print(_line(t, rows[t]))
        if not names:
            print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
