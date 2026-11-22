"""Rule scanner: the proactive alerts with no LLM calls (Kris, 11/6/2026).

The old proactive_scanner asked Haiku about every ticker each cycle (~$25-30/day). This
applies docs/playbook/RULE_CARD.md directly in Python, over Hedgeye's inventory plus the
book and data/stalk/desk_watch.csv, and sends one Telegram message per cycle with only new
alerts (each ticker+action fires once a day; exits fire again at the 3:45 confirm).

  Book longs   below Trend -> OUT · lower high + below Trade -> TO MIN · rp > 1 -> TRIM
  Book shorts  above Trend -> COVER ALL · above Trade -> COVER HALF ·
               +3% / rp <= .25 / range low -> COVER 1/3 (1-share = all)
  New / add long   SS or PM long side (bench included since 11/21, tagged "PM bench only"), price above Trade and Trend, 0 <= rp <= .35, red day,
                   volume falling and under the 20-day average, no earnings within 2 days
  New short        SS or PM short side (or a watch short), below Trade and Trend, 3+ green
                   days, rp >= .65, green today, volume falling, no earnings within 2 days

Our own lines (Vola Trade, 89-day EMA) get the 2% buffer; Keith's numbers don't.

rp uses the bot's one canonical range per name (v_screener, the same one tools/rp_resolve.py and the
other desk tools read): Hedgeye's Risk Range > Hedgeye's Crypto Quant range > Investing Ideas > MFR (5 days) >
TradingView (10 days). Every line prints which one it used and its date (Kris 11/21).
One difference from v_screener (Kris 11/21): a Hedgeye range is used the day it is published and the next
trading day (a weekly Monday range: through Tuesday); after that the name falls through to MFR.

Index rows of the Risk Range (Kris 11/22: "EWG is the DAX"): a fund with no daily Hedgeye range of its own
is read on the index, yield or commodity row it tracks (INDEX_FUND). rp is the index's own price inside
Keith's range for the index, and the side comes from his trend call on the index. A Treasury yield row is
turned upside down for its bond fund (yield at the top of its range = the bond at the bottom).

    py -m tools.rule_scanner --dry-run      # print, don't send or record
    py -m tools.rule_scanner                # send new alerts to Telegram
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = os.path.join(ROOT, "data", "stalk", "desk_watch.csv")
STATE = os.path.join(ROOT, "data", "rule_scanner_state.json")
CASH = {"SPAXX", "FDRXX", "BUXX", "CORE"}
TV_KEY = {"USD": "USD_SEMI"}
LONG_PM = ("active_long", "top_idea_long")
SHORT_PM = ("active_short", "top_idea_short")
LONG_BENCH = ("long_bench",)       # Position Monitor bench (Kris 11/21: read the whole Position Monitor).
SHORT_BENCH = ("short_bench",)     # The weakest source: it only sets a side when nothing else lists the name.
BUF = 0.02
STOP_LONG = 0.05        # card: -5% from cost = close
STOP_SHORT = 0.04       # card: 4% against = cover half
WARN = 0.03             # early warning only, not a card rule (Kris 11/21)
INVERSE_1X = {"NVDD", "METD", "ORCS", "PSQ", "SH", "RWM", "MSFD", "TSLQ", "SKYD"}


def _vol_frac(now: dt.datetime) -> float:
    """Share of a full session's volume traded by now (same curve as desk_rot)."""
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 570:
        return 1.0
    m = max(1, now.hour * 60 + now.minute - 570)
    return min(1.0, 0.25 * min(1, m / 60) + (0.75 * (m - 60) / 330 if m > 60 else 0))


def _fv(x: float) -> str:
    return f"{x / 1e6:.1f}M" if x >= 1e5 else f"{x / 1e3:.0f}k"


def _keith(src: str) -> bool:
    return src.startswith("Keith")


def load_universe():
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select account_name, sum(market_value) from book_positions
                       where snapshot_date=(select max(snapshot_date) from book_positions) group by 1""")
        acct = {a: float(v) for a, v in cur.fetchall()}
        cur.execute("""select symbol, account_name, sum(quantity), sum(cost_basis) from book_positions
                       where snapshot_date=(select max(snapshot_date) from book_positions) and quantity<>0
                       group by 1,2""")
        book = [(s, a, float(q), float(cb or 0)) for s, a, q, cb in cur.fetchall()
                if s and s.isalpha() and len(s) <= 5 and s not in CASH]
        cur.execute("""select distinct on (ticker, side) ticker, side, removed_on from ss_roster_history
                       order by ticker, side, added_on desc""")
        ss_rows = cur.fetchall()
        ss = {(t, s) for t, s, r in ss_rows if r is None}
        ss_removed = {(t, s) for t, s, r in ss_rows if r is not None}
        cur.execute("select ticker, hedgeye_bucket_0629 from ticker_tags where hedgeye_bucket_0629 is not null")
        pm = dict(cur.fetchall())
        cur.execute("""select distinct on (ticker) ticker, bias from hedgeye_etf_pro_ranges
                       where week_of = (select max(week_of) from hedgeye_etf_pro_ranges) order by ticker, week_of desc""")
        etf = dict(cur.fetchall())
    watch = {}
    if os.path.exists(WATCH):
        with open(WATCH, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for t in r["ticker"].split():
                    watch[t] = r["side"]
    for t, side in extra_inventory().items():      # desk_watch.csv wins on a conflict
        if (t, side) in ss_removed:                # card: removed from Signal Strength = off the stalk
            continue
        if t in INDEX_FUND:                        # the index's daily signal beats a watch-file side (Kris 11/22)
            watch[t] = side
        watch.setdefault(t, side)
    return acct, book, ss, pm, etf, watch


# Risk Range rows that are indexes, yields, FX or futures, not tickers you can trade.
# "USD" there is the dollar index; in the book USD is ProShares Ultra Semis, so it is skipped too.
RR_SKIP = {"SPX", "COMPQ", "RUT", "VIX", "USD", "WTIC", "BRENT", "GOLD", "SILVER", "COPPER", "NATGAS",
           "BITCOIN", "NIKK", "DAX", "SSEC", "KOSPI", "UST2Y", "UST10Y", "UST30Y"}
# fund -> (Risk Range row, Yahoo symbol for that row's live price, inverted). Kris approved the map 11/22 and
# asked for the yield rows as the signal for TLT and the other bond funds. Inverted = the fund moves against
# the row (bond prices vs yields): rp becomes 1 - rp and BULLISH on the row means short the fund.
INDEX_FUND = {
    "EWG": ("DAX", "^GDAXI", False), "SPY": ("SPX", "^GSPC", False), "QQQ": ("COMPQ", "^IXIC", False),
    "IWM": ("RUT", "^RUT", False), "EWJ": ("NIKK", "^N225", False), "EWY": ("KOSPI", "^KS11", False),
    "ASHR": ("SSEC", "000001.SS", False), "GLD": ("GOLD", "GC=F", False), "SLV": ("SILVER", "SI=F", False),
    "CPER": ("COPPER", "HG=F", False), "USO": ("WTIC", "CL=F", False), "BNO": ("BRENT", "BZ=F", False),
    "UNG": ("NATGAS", "NG=F", False), "IBIT": ("BITCOIN", "BTC-USD", False), "UUP": ("USD", "DX-Y.NYB", False),
    "TLT": ("UST30Y", "^TYX", True), "ZROZ": ("UST30Y", "^TYX", True), "EDV": ("UST30Y", "^TYX", True),
    "IEF": ("UST10Y", "^TNX", True), "SHY": ("UST2Y", None, True),      # no live 2-year yield on Yahoo
}
LEDGER = os.path.join(ROOT, "data", "stalk", "ledger.csv")
# Everything Hedgeye publishes with a ticker and a side (Kris 11/22: "everything should be on the scanner").
# Signal Strength, the Position Monitor and ETF Pro are read directly in load_universe(); these fill in the
# rest, in this order: Keith's own books first, then his alerts and daily signal, then the analysts' lists,
# then research mentions. Each query returns (ticker, side word); the newest row per ticker is used.
_SIDE = {"long": "long", "bullish": "long", "above": "long", "short": "short", "bearish": "short", "below": "short"}
_DASH = """select ticker, position from hedgeye_dashboard_positions d where product = '{p}' and as_of >= %(d7)s
           and as_of = (select max(as_of) from hedgeye_dashboard_positions where product = d.product)"""
_LAST = ("select distinct on (ticker) ticker, {side} from {tab} where {date} >= %({win})s {extra} "
         "order by ticker, {date} desc{tie}")
SOURCES = (
    ("Portfolio Solutions", _DASH.format(p="portfolio_solutions")),
    ("Go Anywhere portfolio", _DASH.format(p="go_anywhere_portfolio")),
    ("Keith's holdings", _DASH.format(p="overview_holdings")),
    ("Signal Strength dashboard", _DASH.format(p="signal_strength_stocks")),
    ("ETF Pro Plus", _DASH.format(p="etf_pro_plus")),
    ("Investing Ideas", _DASH.format(p="investing_ideas")),
    ("Real-Time Alerts", _LAST.format(side="case when signal_type = 'cover' then null else side end",
                                      tab="hedgeye_rta", date="signal_date", win="d14", extra="", tie=", id desc")),
    ("Keith's signals", _LAST.format(side="side", tab="hedgeye_keiths_signals", date="signal_date", win="d14",
                                     extra="", tie=", id desc")),
    ("Risk Range", """select ticker, trend from hedgeye_risk_ranges
                      where signal_date = (select max(signal_date) from hedgeye_risk_ranges)"""),
    ("Hedgeye trend tag", _LAST.format(side="tag", tab="hedgeye_trend_daily", date="date", win="d7", extra="", tie="")),
    ("Investing Ideas newsletter", _LAST.format(side="side", tab="hedgeye_ii_newsletter", date="signal_date",
                                                win="d14", extra="", tie=", id desc")),
    ("The Call", _LAST.format(side="side", tab="hedgeye_the_call", date="signal_date", win="d14", extra="",
                              tie=", id desc")),
    ("The Call analyst calls", _LAST.format(side="analyst_call", tab="hedgeye_the_call_analyst", date="as_of",
                                            win="d14", extra="", tie="")),
    ("sector monitor", _LAST.format(side="case when tier = 'removed' then null else side end",
                                    tab="hedgeye_sector_monitor_events", date="signal_date", win="d14", extra="",
                                    tie=", id desc")),
    ("Retail", _LAST.format(side="side", tab="hedgeye_retail", date="signal_date", win="d14",
                            extra="and side is not null", tie=", id desc")),
    ("Financials", _LAST.format(side="side", tab="hedgeye_financials", date="signal_date", win="d35", extra="",
                                tie=", id desc")),
    ("HedgAI", _LAST.format(side="case when is_close then null else side end", tab="hedgeye_hedgai",
                            date="signal_date", win="d14", extra="", tie=", id desc")),
    ("Capital Allocation dashboard", _LAST.format(side="trend_status", tab="hedgeye_cap_alloc_dashboard",
                                                  date="as_of", win="d14", extra="", tie="")),
    ("Monthly Trend & Tail", _LAST.format(side="trend_signal", tab="hedgeye_monthly_trend_tail", date="as_of",
                                          win="d35", extra="", tie="")),
    ("Risk Range (rotated out)", _LAST.format(side="trend_signal", tab="hedgeye_risk_range_outbucket",
                                              date="as_of", win="d14", extra="", tie="")),
    ("research", """select distinct on (name) name, direction from research_calls
                    where kind = 'ticker' and signal_date >= %(d14)s
                      and direction in ('long','short','bullish','bearish')
                    order by name, signal_date desc, id desc"""),
    ("MOMO tracker", _LAST.format(side="sentiment", tab="hedgeye_momo", date="signal_date", win="d14",
                                  extra="and sentiment is not null", tie=", id desc")),
)
EXTRA_SRC: dict = {}        # ticker -> which of the sources above put it on the scanner (filled by extra_inventory)
YF_KEY = {"ATZ": "ATZAF"}   # Signal Strength lists Aritzia as ATZ; Yahoo only knows the US line, ATZAF
KEITH_SOURCES = {"The Call", "Macro Show", "KM's Top 3 Things", "Real-Time Alerts"}


def _tradable(t: str) -> bool:
    return (bool(t) and t.isalpha() and t.isupper() and len(t) <= 5 and t not in RR_SKIP and t not in CASH
            and t not in COINS)


def extra_inventory(today: dt.date | None = None) -> dict:
    """The rest of Hedgeye's inventory (Kris 11/20: "the whole universe"): ticker -> 'long' | 'short'.

    Later sources fill in only names the earlier ones don't have:
    1. every table in SOURCES, in its order (Keith's books, his alerts and daily signal, the analysts' lists,
       research mentions);
    2. the sector-call ledger (data/stalk/ledger.csv), last 30 days;
    3. the Risk Range's index, yield and commodity rows, passed to the fund that tracks each (INDEX_FUND).
    Every source is optional: a failure in one never stops the scan.
    """
    today = today or dt.date.today()
    out: dict = {}
    row_side: dict = {}
    keith: dict = {}
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("""select ticker, trend from hedgeye_risk_ranges
                           where signal_date = (select max(signal_date) from hedgeye_risk_ranges)""")
            rows = cur.fetchall()
            row_side = {t: (trend or "").upper() for t, trend in rows}
            for label, sql in SOURCES:              # each one fills only names the ones before it don't have
                try:
                    cur.execute(sql, {"d7": today - dt.timedelta(days=7), "d14": today - dt.timedelta(days=14),
                                      "d35": today - dt.timedelta(days=35)})
                    for t, raw in cur.fetchall():
                        side = _SIDE.get((raw or "").strip().lower())
                        t = (t or "").strip().upper()
                        if side and _tradable(t) and t not in out:
                            out[t] = side
                            EXTRA_SRC[t] = label
                except Exception as e:                               # noqa: BLE001
                    c.rollback()
                    print(f"extra_inventory: {label} skipped ({type(e).__name__})", file=sys.stderr)
    except Exception as e:                                           # noqa: BLE001
        print(f"extra_inventory: database sources skipped ({type(e).__name__})", file=sys.stderr)
    try:
        latest: dict = {}
        # keith: ticker -> (date, side), what Keith himself last said about an index fund, within a week
        with open(LEDGER, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    d = dt.date.fromisoformat(r["date"])
                except (KeyError, ValueError):
                    continue
                if (today - d).days > 30:
                    continue
                call = (r.get("call") or "").lower()
                lg, sh = "long" in call, "short" in call
                side = "long" if lg and not sh and "bearish" not in call else "short" if sh and not lg else None
                t = (r.get("ticker") or "").strip()
                if side and _tradable(t) and d >= latest.get(t, (dt.date.min, None))[0]:
                    latest[t] = (d, side)
                if (r.get("source") or "").strip() in KEITH_SOURCES and (today - d).days <= 7 and t in INDEX_FUND:
                    c2 = re.sub(r"\(.*?\)", "", call)          # "Long (from Short)" is a long
                    k = (None if re.search(r"remove|closed|sold|exit|cover|trim|watch", c2)
                         else "long" if "long" in c2 and "short" not in c2
                         else "short" if "short" in c2 and "long" not in c2 else None)
                    if d >= keith.get(t, (dt.date.min, None))[0]:
                        keith[t] = (d, k)
        for t, (_, side) in latest.items():
            if t not in out:
                out[t] = side
                EXTRA_SRC[t] = "sector-call ledger"
    except OSError:
        pass
    # Keith's daily trend call on an index row is the signal for its fund, and the signal wins (Kris 11/22):
    # it replaces the side from the ledger and the other sources above. load_universe() also puts it over
    # the watch file. Only Hedgeye's own lists (Signal Strength, Position Monitor, ETF Pro) can disagree
    # with it, and then sides() returns 'both': hold what's there, no new entries, until Keith breaks the tie.
    # Keith breaks it by saying so (Kris 11/22, TLT: "keith said long bonds"): a long or short call of his own
    # on the fund in the last 7 days (The Call, Macro Show, Top 3 Things, Real-Time Alerts) is the side.
    for fund, (row, _, inverted) in INDEX_FUND.items():
        side = {"BULLISH": "long", "BEARISH": "short"}.get(row_side.get(row))
        if side:
            out[fund] = {"long": "short", "short": "long"}[side] if inverted else side
        # "long bonds" covers every Treasury fund: the bond funds share Keith's latest call on any of them
        said = (max((keith[f] for f, v in INDEX_FUND.items() if v[2] and f in keith), default=(None, None),
                    key=lambda x: x[0]) if inverted else keith.get(fund, (None, None)))
        if said[1]:
            out[fund] = said[1]
    return out


def sides(t, ss, pm, etf, watch):
    """Which side Hedgeye's inventory has this ticker on: 'long', 'short', 'both' or None.

    Signal Strength is primary (Kris 11/22; the card: it outranks the Position Monitor): a name on one
    Signal Strength list takes that side, whatever the other lists say.
    """
    ss_l, ss_s = (t, "long") in ss, (t, "short") in ss
    if ss_l != ss_s:
        return "long" if ss_l else "short"
    lg = (t, "long") in ss or pm.get(t) in LONG_PM or etf.get(t) == "long" or watch.get(t) == "long"
    sh = (t, "short") in ss or pm.get(t) in SHORT_PM or etf.get(t) == "short" or watch.get(t) == "short"
    if not lg and not sh:
        return "long" if pm.get(t) in LONG_BENCH else "short" if pm.get(t) in SHORT_BENCH else None
    return "both" if lg and sh else "long" if lg else "short" if sh else None


def bench_only(t, ss, pm, etf, watch) -> bool:
    """True when the Position Monitor bench is the only place Hedgeye's inventory lists this ticker."""
    return (pm.get(t) in LONG_BENCH + SHORT_BENCH and t not in etf and t not in watch
            and (t, "long") not in ss and (t, "short") not in ss)


_FALLBACK = (       # same order as v_screener, for names outside its universe (watch-file and ledger names)
    ("hdg", """select distinct on (ticker) ticker, sell_trade, buy_trade, signal_date from hedgeye_risk_ranges
               where ticker=any(%s) and buy_trade is not null and sell_trade > buy_trade
                 and signal_date >= current_date - 7 order by ticker, signal_date desc"""),
    ("iin", """select distinct on (ticker) ticker, range_high, range_low, signal_date from hedgeye_ii_newsletter
               where ticker=any(%s) and range_low is not null and range_high > range_low
                 and signal_date >= current_date - 7 order by ticker, signal_date desc"""),
    ("mfr", """select distinct on (ticker) ticker, range_high, range_low, snapshot_date from mfr_snapshots
               where ticker=any(%s) and range_low is not null and range_high > range_low
                 and snapshot_date >= current_date - 5 order by ticker, snapshot_date desc, fetched_at desc"""),
    ("tv", """select distinct on (ticker) ticker, range_high, range_low, bar_date from tv_levels
              where ticker=any(%s) and range_high is not null and range_high > range_low
                and bar_date >= current_date - 10 order by ticker, bar_date desc"""),
)


HEDGEYE_SRC = ("hdg", "cq", "iin")
COINS = {"BTC", "ETH", "SOL", "AVAX", "XRP", "DOGE", "ADA", "BNB", "LINK", "SUI", "HYPE"}   # coin labels, not stock tickers
_RANGE_RE = re.compile(r"(?<![\d.,/$-])(\d[\d,]*\.?\d*)\s*[-–]\s*(\d[\d,]*\.?\d*)(?![\d/])")


def crypto_quant_ranges(cur, T) -> dict:
    """Hedgeye's daily Crypto Quant ranges (Kris 11/21: MARA, COIN, MSTR, IBIT... get a range every day there).

    The numbers live only in the research digest's text ("Bearish TREND; 5.67-7.43 (from 5.70-7.54), price
    6.25"), so the first low-high pair in the latest note is read, and dropped if it doesn't look like a range.
    """
    out: dict = {}
    names = [t for t in T if t not in COINS]
    cur.execute("""select distinct on (name) name, signal_date, thesis from research_calls
                   where product='crypto_quant' and name=any(%s) and signal_date >= current_date - 7
                   order by name, signal_date desc, id desc""", (names,))
    for t, d, thesis in cur.fetchall():
        m = _RANGE_RE.search(thesis or "")
        if not m:
            continue
        lo, hi = (float(x.replace(",", "")) for x in m.groups())
        if 0 < lo < hi < lo * 2 and _hedgeye_fresh(d):
            out[t] = (hi, lo, "cq", d)
    return out
HEDGEYE_RANGE_DAYS = 1      # Kris 11/21: the daily Risk Range always wins; a weekly range (Monday) is good through
                            # Tuesday, then MFR's is used. One weekday of age covers both.


def _hedgeye_fresh(asof, today: dt.date | None = None) -> bool:
    """True when a Hedgeye-published range is at most HEDGEYE_RANGE_DAYS weekdays old."""
    if asof is None:
        return False
    today = today or dt.date.today()
    age, d = 0, asof
    while d < today:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            age += 1
    return age <= HEDGEYE_RANGE_DAYS


def load_ranges(T) -> dict:
    """ticker -> (range_high, range_low, source, as-of date), from the bot's one canonical range.

    v_screener picks it (hdg 7d > iin 7d > mfr 5d > tv 10d); names it doesn't carry go through the same
    four sources in the same order. Hedgeye's Risk Range table has index and commodity rows whose labels
    collide with stock tickers (RR_SKIP), so those never take the 'hdg' range.
    """
    import db_pg
    out: dict = {}
    with db_pg.get_conn() as c, c.cursor() as cur:
        try:
            cq = crypto_quant_ranges(cur, T)
        except Exception as e:                                      # noqa: BLE001
            c.rollback()
            cq = {}
            print(f"load_ranges: Crypto Quant ranges skipped ({type(e).__name__})", file=sys.stderr)
        cur.execute("""select ticker, range_high, range_low, band_source, range_asof from v_screener
                       where ticker=any(%s) and range_low is not null and range_high > range_low""", (list(T),))
        for t, hi, lo, src, asof in cur.fetchall():
            if src == "hdg" and t in RR_SKIP:
                continue
            if src in HEDGEYE_SRC and not _hedgeye_fresh(asof):
                continue                                # too old: falls through to MFR below
            if t in cq and src != "hdg":
                continue                                # a daily Crypto Quant range beats everything but the daily Risk Range
            out[t] = (float(hi), float(lo), src or "?", asof)
        for t, v in cq.items():
            out.setdefault(t, v)
        for src, sql in _FALLBACK:
            need = [t for t in T if t not in out and not (src == "hdg" and t in RR_SKIP)]
            if not need:
                break
            back = {TV_KEY.get(t, t): t for t in need} if src == "tv" else {t: t for t in need}
            cur.execute(sql, (list(back),))
            for k, hi, lo, asof in cur.fetchall():
                if src in HEDGEYE_SRC and not _hedgeye_fresh(asof):
                    continue
                out[back[k]] = (float(hi), float(lo), src, asof)
    return out


def index_ranges(T, rng) -> dict:
    """fund -> dict(hi, lo, row, asof, sym, inv) for funds read on a Risk Range index row.

    Only for a fund with no fresh daily Hedgeye range of its own (its own Risk Range or Crypto Quant range
    wins).
    """
    import db_pg
    funds = [t for t in T if t in INDEX_FUND and rng.get(t, (None,) * 4)[2] not in ("hdg", "cq")]
    if not funds:
        return {}
    out: dict = {}
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select distinct on (ticker) ticker, sell_trade, buy_trade, signal_date
                       from hedgeye_risk_ranges
                       where ticker=any(%s) and buy_trade is not null and sell_trade > buy_trade
                         and signal_date >= current_date - 7 order by ticker, signal_date desc""",
                    (sorted({INDEX_FUND[f][0] for f in funds}),))
        rows = {r[0]: r[1:] for r in cur.fetchall()}
    for f in funds:
        row, sym, inv = INDEX_FUND[f]
        if row in rows and _hedgeye_fresh(rows[row][2]):
            hi, lo, asof = rows[row]
            out[f] = dict(hi=float(hi), lo=float(lo), row=row, asof=asof, sym=sym, inv=inv)
    return out


def index_prices(idx: dict) -> dict:
    """Risk Range row -> its price now: Yahoo's last print for the row's symbol.

    Hedgeye's metals are spot and Yahoo's are the front future, so those can sit a few tenths of a percent
    apart. A row with no symbol (the 2-year yield: Yahoo has no live one) gets no price, so no rp.
    """
    import yfinance as yf
    out: dict = {}
    for v in {x["row"]: x for x in idx.values()}.values():
        if not v["sym"]:
            continue
        try:
            h = yf.Ticker(v["sym"]).history(period="5d", interval="1d").Close.dropna()
            out[v["row"]] = float(h.iloc[-1])
        except Exception:                                           # noqa: BLE001
            continue
    return out


def earnings_soon(t: str, days: int = 2) -> bool:
    import yfinance as yf
    try:
        cal = yf.Ticker(t).calendar or {}
        ds = cal.get("Earnings Date") or []
        today = dt.date.today()
        return any(0 <= (d - today).days <= days for d in ds if isinstance(d, dt.date))
    except Exception:
        return False


def is_etf(t: str) -> bool:
    import yfinance as yf
    try:
        return (yf.Ticker(t).info or {}).get("quoteType") == "ETF"
    except Exception:
        return False


def scan(now: dt.datetime | None = None) -> list[dict]:
    import db_pg
    import yfinance as yf
    from tools.level_lines import lookup
    from tools.range_structure import structure
    from tools.rp_resolve import is_low_signal

    now = now or dt.datetime.now()
    acct, book, ss, pm, etf, watch = load_universe()
    held = {}
    for s, a, q, cb in book:
        held.setdefault(s, []).append((a, q, cb))
    inv = ({t for t, _ in ss} | {t for t, b in pm.items() if b in LONG_PM + SHORT_PM + LONG_BENCH + SHORT_BENCH}
           | set(etf) | set(watch))
    T = sorted(t for t in inv | set(held) if t.isalpha() and len(t) <= 5)     # US listings; skips EL.PA, ETHUSD
    rng = load_ranges(T)
    # A bench-only name with no fresh range can't produce a line (rp is unknown), and each extra name costs
    # about a second of lookups, so leave those out. They come in once the capture desk has a range for them.
    idx = index_ranges(T, rng)
    idx_px = index_prices(idx)
    T = [t for t in T if t in held or t in rng or t in idx or not bench_only(t, ss, pm, etf, watch)]
    with db_pg.get_conn() as c, c.cursor() as cur:
        # MFR implied vs realized vol (Kris 11/20): context on every line, never a gate
        cur.execute("""select distinct on (ticker) ticker, iv, rv from mfr_snapshots
                       where ticker=any(%s) and iv is not null and rv is not null and rv > 0
                         and snapshot_date >= current_date - 5
                       order by ticker, snapshot_date desc, fetched_at desc""", (T,))
        ivrv = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
    lv = lookup(T)
    rs = structure(T)
    d = yf.download([YF_KEY.get(t, t) for t in T], period="3mo", interval="1d", progress=False, threads=True,
                    group_by="ticker")
    fr = _vol_frac(now)
    closing = now.hour * 60 + now.minute >= 15 * 60 + 40
    out = []
    for t in T:
        try:
            h = d[YF_KEY.get(t, t)].dropna()
        except Exception:
            continue
        if len(h) < 25:
            continue
        cl = h.Close.values
        vo = h.Volume.values.astype(float)
        p = float(cl[-1])
        day = (cl[-1] / cl[-2] - 1) * 100
        a20 = vo[-21:-1].mean()
        if h.index[-1].date() == now.date():    # before the first print, the last bar is still yesterday's
            vo[-1] /= fr
        tr, tr_src = lv[t]["trade"][0], lv[t]["trade"][1]
        tn_l, tn_l_src = lv[t]["trend_long"][0], lv[t]["trend_long"][1]     # yellow cloud bottom
        tn_s, tn_s_src = lv[t]["trend_short"][0], lv[t]["trend_short"][1]   # yellow cloud top
        rh, rl, r_src, r_asof = rng.get(t, (None, None, None, None))
        rp = (p - rl) / (rh - rl) if rh and rl and rh > rl else None
        rtag = f"{r_src} {r_asof:%m/%d}" if r_src and r_asof else (r_src or "")
        if rp is not None and is_low_signal(rl, rh, p):     # range under 2% of price: rp is noise (cash-like funds)
            rp, rtag = None, "range too narrow"
        if t in idx:            # read on the index row itself: its price in Keith's range (card: never mix the two)
            x = idx[t]
            ip = idx_px.get(x["row"])
            rh = rl = None      # the index's range is not in the fund's prices
            rp = (ip - x["lo"]) / (x["hi"] - x["lo"]) if ip else None
            if rp is not None and x["inv"]:
                rp = 1 - rp
            rtag = (f"hdg {x['row']}{' upside down' if x['inv'] else ''} {x['asof']:%m/%d}" if ip
                    else f"no live price for {x['row']}")
        tb = 0 if _keith(tr_src) else BUF
        nb_l = BUF if tn_l_src.endswith("EMA") else 0       # the buffer is only for the EMA fallback
        nb_s = BUF if tn_s_src.endswith("EMA") else 0
        lower_high = rs.get(t, {}).get("lower_high", False)
        streak = 0
        for k in range(len(cl) - 1, 0, -1):
            if (cl[k] > cl[k - 1]) == (day > 0) and cl[k] != cl[k - 1]:
                streak += 1
            else:
                break
        # decreasing volume (Kris 11/6): the pullback/bounce days average under the 20-day and today is lighter than yesterday
        n = max(streak, 2)
        vol_falling = vo[-n:].mean() < a20 and vo[-1] < vo[-2]
        base = dict(ticker=t, px=p, day=day, rp=rp, trade=tr, trend=tn_l, rl=rl, rh=rh,
                    vol3="/".join(_fv(x) for x in vo[-3:]), avg=_fv(a20), streak=streak,
                    ivrv=ivrv.get(t), rtag=rtag)
        side = sides(t, ss, pm, etf, watch)
        src = "; PM bench only" if bench_only(t, ss, pm, etf, watch) else ""
        if (not src and t in EXTRA_SRC and t not in etf and pm.get(t) is None
                and (t, "long") not in ss and (t, "short") not in ss):
            src = f"; from {EXTRA_SRC[t]}"

        for a, q, cb in held.get(t, []):
            mv = q * p
            pct = abs(mv) / acct.get(a, 1) * 100
            tag = "" if closing else " (if it closes here)"
            # price stops off cost (Kris 11/21; card: longs -5% = close, shorts 4% against = cover half).
            # A 1x inverse ETF held long is a short: 4% under its cost = 4% against.
            avg_c = abs(cb / q) if q and cb else None
            vs = (p / avg_c - 1) if avg_c else None             # long: negative = losing
            inv = q > 0 and t in INVERSE_1X
            stopped = False
            if vs is not None and q > 0 and not inv:
                if vs <= -STOP_LONG:
                    out.append(dict(base, acct=a, action="SELL ALL", exit=True,
                                    why=f"STOP: {vs * 100:+.1f}% from cost {avg_c:.2f} (stop {avg_c * (1 - STOP_LONG):.2f}); check the print is real"))
                    stopped = True
                elif vs <= -WARN:
                    out.append(dict(base, acct=a, action="WATCH", why=f"{vs * 100:+.1f}% from cost {avg_c:.2f}; "
                                    f"stop at {avg_c * (1 - STOP_LONG):.2f}, no adds"))
            elif vs is not None and (q < 0 or inv):
                against = vs if q < 0 else -vs                  # short: price up = against; inverse ETF: its price down
                lvl = avg_c * (1 + STOP_SHORT) if q < 0 else avg_c * (1 - STOP_SHORT)
                if against >= STOP_SHORT:
                    half = f"{max(1, math.ceil(abs(q) / 2)):g}" if q < 0 else f"{abs(q) / 2:.3f} sh"
                    out.append(dict(base, acct=a, action=f"{'COVER' if q < 0 else 'SELL'} {half}", exit=True,
                                    why=f"STOP: {against * 100:.1f}% against (entry {avg_c:.2f}, line {lvl:.2f}) = cover half"))
                    stopped = True
                elif against >= WARN:
                    out.append(dict(base, acct=a, action="WATCH", why=f"{against * 100:.1f}% against (entry {avg_c:.2f}); "
                                    f"cover half at {lvl:.2f}, no adds"))
            if stopped:
                continue
            if q > 0:
                if tn_l and p < tn_l * (1 - nb_l):
                    out.append(dict(base, acct=a, action="SELL ALL", why=f"below Trend {tn_l:.2f}{tag}", exit=True))
                elif lower_high and tr and p < tr * (1 - tb):
                    floor = (2 if is_etf(t) else 1) / 100 * acct[a]      # class MIN; at MIN it's held to Trend
                    if mv > floor * 1.15:
                        out.append(dict(base, acct=a, action=f"SELL {(mv - floor) / p:.1f} sh TO MIN",
                                        why=f"lower high + below Trade {tr:.2f}{tag}", exit=True))
                elif rp is not None and rp > 1 and mv > (2 if is_etf(t) else 1) / 100 * acct[a] * 1.15:
                    out.append(dict(base, acct=a, action=f"TRIM ~${0.005 * acct[a]:.0f}", why="rp > 1"))
            else:
                qa = abs(q)
                avg = cb / qa if qa else p
                pnl = (avg / p - 1) * 100
                third = 1 if qa < 2 else max(1, math.floor(qa / 3))
                if tn_s and p > tn_s * (1 + nb_s):
                    out.append(dict(base, acct=a, action=f"COVER ALL {qa:g}", why=f"above Trend {tn_s:.2f}{tag}", exit=True))
                elif tr and p > tr * (1 + tb):
                    out.append(dict(base, acct=a, action=f"COVER {max(1, math.ceil(qa / 2)):g}", why=f"above Trade {tr:.2f}{tag}", exit=True))
                elif pnl >= 3 or (rp is not None and rp <= 0.25) or (rl and p <= rl):
                    n = qa if qa < 2 else third
                    out.append(dict(base, acct=a, action=f"COVER {n:g}", why=f"+{pnl:.0f}% / rp {rp if rp is None else round(rp, 2)}"))

        if side in ("long",) and rp is not None and tr and tn_l:
            if p > tn_l and p >= tr * (1 - tb) and 0 <= rp <= 0.35 and day < 0 and vol_falling:
                longs = [x for x in held.get(t, []) if x[1] > 0]
                a = longs[0][0] if longs else "Rollover IRA"
                size = 0.005 * acct[a] if longs else 0.01 * acct[a]
                out.append(dict(base, acct=a, action=f"{'ADD' if longs else 'BUY'} ~${size:.0f}",
                                why=f"rp {rp:.2f}, red, vol falling; out under {tn_l:.2f}{src}", entry=True))
        if side in ("short",) and rp is not None and tr and tn_s and not any(x[1] < 0 for x in held.get(t, [])):
            if p < tn_s and p < tr and streak >= 3 and day > 0 and rp >= 0.65 and vol_falling:
                a = "Individual"
                out.append(dict(base, acct=a, action=f"SHORT ~${0.006 * acct[a]:.0f}",
                                why=f"day {streak} bounce, rp {rp:.2f}, vol falling; stop {tr:.2f}{src}", entry=True))
    for o in out:
        if o.get("entry") and earnings_soon(o["ticker"]):
            o["skip"] = "earnings within 2 days"
    return [o for o in out if not o.get("skip")]


def _key(o, closing):
    return f"{o['acct']}|{o['ticker']}|{o['action'].split()[0]}|{'close' if o.get('exit') and closing else 'day'}"


def fresh(alerts, now, record):
    today = now.date().isoformat()
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    seen = set(st.get(today, []))
    closing = now.hour * 60 + now.minute >= 15 * 60 + 40
    new = [o for o in alerts if _key(o, closing) not in seen]
    if record and new:
        st = {today: sorted(seen | {_key(o, closing) for o in new})}
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    return new


def fmt(alerts, now):
    lines = [f"as of {now:%H:%M}"]
    for o in sorted(alerts, key=lambda o: (not o.get("exit"), o["acct"], o["ticker"])):
        rp = "-" if o["rp"] is None else f"{o['rp']:.2f}"
        if o.get("rtag"):
            rp += f" ({o['rtag']})"
        iv = ""
        if o.get("ivrv"):
            i, r = o["ivrv"]
            iv = f" | IV {i * 100:.0f} vs RV {r * 100:.0f} ({(i / r - 1) * 100:+.0f}%)"
        lines.append(f"{o['acct'].split()[0]}: {o['action']} {o['ticker']} @ {o['px']:.2f} | rp {rp} | "
                     f"vol {o['vol3']} (avg {o['avg']}){iv} | {o['why']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print every alert; don't send or record")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    now = dt.datetime.now()
    if not args.dry_run:
        from parser_tms_deck import load_telegram_env
        from tools.trading_calendar import is_trading_day
        if not is_trading_day(now.date()):
            print(f"{now:%H:%M} market closed")
            return 0
        load_telegram_env()     # the laptop .env has no Telegram settings
    alerts = scan(now)
    new = alerts if args.dry_run else fresh(alerts, now, record=False)
    if not new:
        print(f"{now:%H:%M} no new alerts ({len(alerts)} standing)")
        return 0
    msg = fmt(new, now)
    print(msg)
    if args.dry_run:
        return 0
    from notifier import send_telegram
    if not send_telegram("Rule scanner", msg):
        print("Telegram send failed; alerts stay unrecorded and retry next cycle")
        return 1
    fresh(new, now, record=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
