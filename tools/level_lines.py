"""Trade / Trend lines.

    TRADE = Vola2vola Trade line (Kris, 11/5/2026)
    TREND = the yellow MFR Trend cloud: bottom for longs, top for shorts (Kris, 11/9/2026)

Line source (Kris, 11/15/2026): MFR first (mfr_indicators: vola_trade, trend_bear = cloud bottom,
trend_bull = cloud top), TradingView (tv_levels) as the backup, via the v_line_source view (migration
140): the newest bar wins, a tie goes to MFR.

The 34-day EMA is stored too and is the Trade fallback when a name has no Vola
bar from the last 5 days. EMAs are stored nightly in level_lines; v_screener
reads the lines (migration 127), so every screen and alert uses the same numbers.

lookup() answers "what's the level for this ticker": Keith's number first
(keith_levels), then his ranges (ETF Pro, Risk Range), then Vola and the EMAs,
with the source and how far to trust it.

    py -m tools.level_lines --refresh
    py -m tools.level_lines --lookup SCHA BNO OKTA
    py -m tools.level_lines --load-keith <levels_keith.csv>
    py -m tools.level_lines --load-tt
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import re
import sys
from zoneinfo import ZoneInfo

TRADE_SPAN = 34
TREND_SPAN = 89
MIN_BARS = 150           # enough for the 89-day EMA to settle
ALPHA_MAX_AGE = 5        # days an Alpha Code stays current
TT_MAX_AGE = 35          # the Trend & Tail email is monthly (Tail still read this long)
KEITH_TREND_MAX_AGE = 7  # Kris 11/14: Keith's numbers win for one week, then the cloud does
VOLA_MAX_AGE = 5         # days a Vola Trade bar stays current
PROXY = {"SCHA": "IWM", "VTWO": "IWM"}
SYMBOL_OVERRIDE = {"USD": "USD"}             # the 2x semis ETF we hold, not the dollar index
TV_KEY = {"USD": "USD_SEMI"}                 # tv_levels' USD key is the dollar index
NO_YAHOO = {"UST2Y", "CAD/USD", "USD_SEMI"}  # Yahoo's series is a different instrument, or no such symbol
MAX_GAP = 0.40                               # vs TradingView's close: wrong instrument or a split


_ETF_LATEST = "(SELECT max(week_of) FROM hedgeye_etf_pro_ranges)"

def ema_lines(closes) -> tuple[float, float] | None:
    """Pure: (trade, trend) from a list of daily closes, oldest first."""
    if len(closes) < MIN_BARS:
        return None
    out = []
    for span in (TRADE_SPAN, TREND_SPAN):
        a, e = 2 / (span + 1), float(closes[0])
        for c in closes[1:]:
            e = a * float(c) + (1 - a) * e
        out.append(e)
    return out[0], out[1]


def completed_bars(idx_dates, now_et: dt.datetime) -> int:
    """Pure: how many bars are finished (today's doesn't count before 4:15 PM ET)."""
    n = len(idx_dates)
    if n and idx_dates[-1] == now_et.date() and (now_et.hour, now_et.minute) < (16, 15):
        return n - 1
    return n


def _universe() -> list[str]:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM v_screener UNION SELECT DISTINCT ticker FROM tv_levels")
        return [r[0] for r in cur.fetchall()]


def refresh(tickers: list[str] | None = None, chunk: int = 150) -> dict:
    """Recompute and store the lines from Yahoo daily closes."""
    import warnings
    import db_pg
    import yfinance as yf
    from tools.quotes import yahoo_symbol
    warnings.filterwarnings("ignore")
    tickers = tickers or _universe()
    now_et = dt.datetime.now(ZoneInfo("America/New_York"))
    sym = {t: None if t in NO_YAHOO else SYMBOL_OVERRIDE.get(t) or yahoo_symbol(t) for t in tickers}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ON (ticker) ticker, bar_close FROM tv_levels WHERE bar_close IS NOT NULL "
                    "ORDER BY ticker, bar_date DESC")
        tv_close = {t: float(c) for t, c in cur.fetchall()}
    by_sym = {}
    for t, s in sym.items():
        if s:
            by_sym.setdefault(s, []).append(t)
    syms, rows, thin, gapped = sorted(by_sym), [], 0, []
    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        d = yf.download(part, period="3y", interval="1d", auto_adjust=False, progress=False,
                        threads=True, group_by="ticker")
        for s in part:
            try:
                c = (d[s] if len(part) > 1 else d)["Close"].dropna()
            except Exception:  # noqa: BLE001
                continue
            dates = [x.date() for x in c.index]
            n = completed_bars(dates, now_et)
            lines = ema_lines([float(x) for x in c.values[:n]])
            if not lines:
                thin += 1
                continue
            last = float(c.values[n - 1])
            for t in by_sym[s]:
                if t in tv_close and abs(last / tv_close[t] - 1) > MAX_GAP:
                    gapped.append(t)
                    continue
                rows.append((t, dates[n - 1], lines[0], lines[1], last))
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM level_lines WHERE ticker = ANY(%s)", (gapped + sorted(NO_YAHOO),))
        cur.executemany(
            "INSERT INTO level_lines (ticker, bar_date, ema34, ema89, bar_close) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (ticker, bar_date) DO UPDATE SET ema34=EXCLUDED.ema34, ema89=EXCLUDED.ema89, "
            "bar_close=EXCLUDED.bar_close, computed_at=now()", rows)
        conn.commit()
    return {"asked": len(tickers), "stored": len(rows), "too_short": thin, "gapped": gapped,
            "no_symbol": sum(1 for s in sym.values() if not s)}


def _store_keith(rows: list[tuple]) -> int:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO keith_levels (ticker, as_of, source, trade, trend, tail) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (ticker, as_of, source) DO UPDATE SET trade=EXCLUDED.trade, trend=EXCLUDED.trend, "
            "tail=EXCLUDED.tail", rows)
        conn.commit()
    return len(rows)


def load_keith_csv(path: str) -> int:
    """Alpha Codes harvested by hand: source, source_date, ticker, trade, trend, ..."""
    num = lambda x: float(x) if x not in ("", None) else None
    with open(path, encoding="utf-8", newline="") as f:
        rows = [(r["ticker"], r["source_date"], "alpha_code", num(r.get("trade")), num(r.get("trend")), None)
                for r in csv.DictReader(f)]
    # one row per ticker + date: keep the last mention
    return _store_keith(list({(r[0], r[1]): r for r in rows}.values()))


def parse_trend_tail(body: str) -> list[dict]:
    """Pure: rows of the 'Trend & Tail Levels | Monthly Dashboard' email."""
    t = html.unescape(re.sub(r"<(style|script).*?</\1>", "", body or "", flags=re.S | re.I))
    cells = [x.strip() for x in re.sub(r"<[^>]+>", "\n", t).split("\n") if x.strip()]
    num, arrows, out, i = re.compile(r"^\d[\d,]*\.?\d*$"), "▲▼", [], 0
    f = lambda s: float(s.replace(",", ""))
    while i < len(cells) - 5:
        if (re.fullmatch(r"[A-Z]{2,5}", cells[i]) and cells[i + 1] in arrows and num.match(cells[i + 2])
                and num.match(cells[i + 3]) and cells[i + 4] in ("Above", "Below")):
            j = i + 5 + (cells[i + 5] in arrows)
            if num.match(cells[j]) and cells[j + 1] in ("Above", "Below"):
                out.append({"t": cells[i], "trend": f(cells[i + 3]), "tail": f(cells[j])})
                i = j + 2
                continue
        i += 1
    return out


def load_trend_tail() -> int:
    """Store the newest Trend & Tail email from hedgeye_emails_raw."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT received_at, html_body FROM hedgeye_emails_raw "
                    "WHERE subject ILIKE 'Trend & Tail%%' ORDER BY received_at DESC LIMIT 1")
        row = cur.fetchone()
    if not row:
        return 0
    return _store_keith([(r["t"], row[0].date(), "trend_tail", None, r["trend"], r["tail"])
                         for r in parse_trend_tail(row[1])])


def pick_level(kind: str, alpha, tt, ema, macro: bool, vola=None, vola_src=None) -> tuple:
    """Pure: (level, source, trust) for 'trade' or 'trend'."""
    if alpha is not None:
        return alpha, "Keith Alpha Code", "his number"
    if kind == "trend" and tt is not None:
        return tt, "Keith Trend & Tail email", "his number"
    if kind == "trade" and vola is not None:
        return vola, f"Vola2vola Trade ({vola_src})" if vola_src else "Vola2vola Trade", "Kris's line; use the 2% buffer"
    if ema is None:
        return None, "no data", "none"
    trust = "good, usually within 1% of Keith" if macro else "rough, thin test on stocks; use the 2% buffer"
    return ema, f"{TRADE_SPAN if kind == 'trade' else TREND_SPAN}-day EMA", trust


def side_trends(picked: tuple, cloud: tuple | None, src: str | None = None) -> tuple[tuple, tuple]:
    """Pure: (long Trend, short Trend). Keith's number wins; otherwise the yellow MFR cloud
    (Kris, 11/9/2026): bottom for longs, top for shorts. EMA 89 only if there's no cloud."""
    if picked[1].startswith("Keith") or cloud is None:
        return picked, picked
    upper, lower = cloud
    tag = f" ({src})" if src else ""
    return (lower, f"Yellow cloud bottom{tag}", "Kris's line"), (upper, f"Yellow cloud top{tag}", "Kris's line")


def lookup(tickers: list[str]) -> dict:
    import db_pg
    today = dt.date.today()
    out = {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for t in tickers:
            src = PROXY.get(t, t)
            cur.execute("SELECT trade, as_of FROM keith_levels WHERE ticker=%s AND source='alpha_code' "
                        "AND trade IS NOT NULL AND as_of >= %s ORDER BY as_of DESC LIMIT 1",
                        (src, today - dt.timedelta(days=ALPHA_MAX_AGE)))
            a_tr = cur.fetchone()
            cur.execute("SELECT trend, as_of FROM keith_levels WHERE ticker=%s AND source='alpha_code' "
                        "AND trend IS NOT NULL AND as_of >= %s ORDER BY as_of DESC LIMIT 1",
                        (src, today - dt.timedelta(days=ALPHA_MAX_AGE)))
            a_tn = cur.fetchone()
            cur.execute("SELECT trend, tail, as_of FROM keith_levels WHERE ticker=%s AND source='trend_tail' "
                        "AND as_of >= %s ORDER BY as_of DESC LIMIT 1", (src, today - dt.timedelta(days=TT_MAX_AGE)))
            tt = cur.fetchone()
            cur.execute("SELECT ema34, ema89, bar_date, bar_close FROM level_lines WHERE ticker=%s "
                        "ORDER BY bar_date DESC LIMIT 1", (src,))
            ll = cur.fetchone()
            # 11/19: latest week only. A name dropped from ETF Pro kept its last bias forever (QAT, removed
            # 11/2, still showed "short 14.76-15.89" from week 10/26 and the desk acted on it).
            cur.execute("SELECT range_low, range_high, bias, week_of FROM hedgeye_etf_pro_ranges WHERE ticker=%s "
                        f"AND week_of = {_ETF_LATEST}", (src,))
            ep = cur.fetchone()
            cur.execute("SELECT 1 FROM hedgeye_etf_pro_ranges WHERE ticker=%s LIMIT 1", (src,))
            ep_ever = cur.fetchone() is not None       # "is a macro ETF" flag: any week, as before
            cur.execute("SELECT trade, cloud_upper, cloud_lower, bar_date, src FROM v_line_source WHERE key=%s",
                        (TV_KEY.get(src, src),))
            ls = cur.fetchone()
            vb = ls if ls and ls[0] is not None and ls[3] >= today - dt.timedelta(days=VOLA_MAX_AGE) else None
            # 11/19: current list only. Risk Range Signals carries the same ~35 names daily; a name that dropped
            # off kept its old range forever (XLP showed 79.95-83.25 from 2025-05-22).
            cur.execute("SELECT buy_trade, sell_trade, trend, signal_date FROM hedgeye_risk_ranges "
                        "WHERE ticker=%s AND buy_trade IS NOT NULL "
                        "AND signal_date >= (SELECT max(signal_date) FROM hedgeye_risk_ranges) - 4 "
                        "ORDER BY signal_date DESC LIMIT 1", (src,))
            rr = cur.fetchone() if t != "USD" else None      # his USD range is the dollar index
            g = lambda x: float(x) if x is not None else None
            cur.execute("SELECT 1 FROM tv_levels WHERE ticker=%s AND tv_symbol !~ '^(BATS|OTC|NYSE|NASDAQ|AMEX)' LIMIT 1", (src,))
            macro_row = cur.fetchone()
            macro = bool(macro_row) or tt is not None or ep_ever
            trade = pick_level("trade", g(a_tr[0]) if a_tr else None, None, g(ll[0]) if ll else None, macro,
                               vola=g(vb[0]) if vb else None, vola_src=vb[4] if vb else None)
            tt_fresh = tt is not None and tt[2] >= today - dt.timedelta(days=KEITH_TREND_MAX_AGE)
            trend = pick_level("trend", g(a_tn[0]) if a_tn else None, g(tt[0]) if tt_fresh else None,
                               g(ll[1]) if ll else None, macro)
            fresh_cloud = ls and ls[1] is not None and ls[2] is not None and ls[3] >= today - dt.timedelta(days=VOLA_MAX_AGE)
            cloud = (g(ls[1]), g(ls[2])) if fresh_cloud else None
            trend_long, trend_short = side_trends(trend, cloud, ls[4] if fresh_cloud else None)
            cur.execute("SELECT price FROM mfr_snapshots WHERE ticker=%s AND price IS NOT NULL "
                        "ORDER BY snapshot_date DESC LIMIT 1", (src,))
            px = cur.fetchone()
            close = g(px[0]) if px else (g(ll[3]) if ll else None)
            if cloud and not trend[1].startswith("Keith"):
                trend = trend_long if close is not None and close >= sum(cloud) / 2 else trend_short
            out[t] = {"proxy": src if src != t else None, "trade": trade, "trend": trend,
                      "trend_long": trend_long, "trend_short": trend_short,
                      "tail": g(tt[1]) if tt else None,
                      # 11/19: "bar" was the EMA table's date only (refreshed inside morning_brief), so at 07:35
                      # every name read 11/17 while its Trade/cloud were already MFR 11/18. Now the newest of the
                      # line source (MFR/TV) bar and the EMA bar; both kept for checks.
                      "bar": max([d for d in ((ls[3] if ls else None), (ll[2] if ll else None)) if d], default=None),
                      "line_bar": ls[3] if ls else None, "ema_bar": ll[2] if ll else None,
                      "etf_pro": (g(ep[0]), g(ep[1]), ep[2], ep[3]) if ep else None,
                      "risk_range": (g(rr[0]), g(rr[1]), rr[2], rr[3]) if rr else None}
            if src != t:
                # Kris 11/14: a proxied ETF is read on the proxy alone. Every proxy line is scaled into the
                # ETF's price by the same-bar close ratio, which equals comparing the proxy's price to its own lines.
                # Same-bar closes: TradingView first, then the Yahoo closes in level_lines (11/15: the TV run
                # may stop covering names MFR has).
                cur.execute("SELECT a.bar_close / b.bar_close, a.bar_date FROM tv_levels a JOIN tv_levels b "
                            "ON b.bar_date = a.bar_date AND b.ticker = %s WHERE a.ticker = %s "
                            "AND a.bar_close IS NOT NULL AND b.bar_close IS NOT NULL AND a.bar_date >= %s "
                            "ORDER BY a.bar_date DESC LIMIT 1",
                            (TV_KEY.get(src, src), TV_KEY.get(t, t), today - dt.timedelta(days=VOLA_MAX_AGE)))
                rt = cur.fetchone()
                if not rt:
                    cur.execute("SELECT a.bar_close / b.bar_close, a.bar_date FROM level_lines a JOIN level_lines b "
                                "ON b.bar_date = a.bar_date AND b.ticker = %s WHERE a.ticker = %s "
                                "AND a.bar_close IS NOT NULL AND b.bar_close IS NOT NULL ORDER BY a.bar_date DESC LIMIT 1",
                                (src, t))
                    rt = cur.fetchone()
                out[t] = _scale(out[t], float(rt[0]), rt[1]) if rt else out[t]
    return out


def _scale(d: dict, r: float, on) -> dict:
    """Scale a proxy's levels into the proxied ETF's price (ratio r = ETF close / proxy close on bar `on`)."""
    sc = lambda x: x * r if isinstance(x, (int, float)) else x
    lvl = lambda v: (sc(v[0]),) + tuple(v[1:]) if v else v
    d = dict(d)
    for k in ("trade", "trend", "trend_long", "trend_short"):
        d[k] = lvl(d[k])
    d["tail"] = sc(d["tail"])
    if d["etf_pro"]:
        d["etf_pro"] = (sc(d["etf_pro"][0]), sc(d["etf_pro"][1])) + tuple(d["etf_pro"][2:])
    if d["risk_range"]:
        d["risk_range"] = (sc(d["risk_range"][0]), sc(d["risk_range"][1]), d["risk_range"][2], d["risk_range"][3])
    d["proxy_ratio"] = (round(r, 6), on)
    return d


def _cli() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--refresh", action="store_true")
    g.add_argument("--lookup", nargs="+")
    g.add_argument("--load-keith")
    g.add_argument("--load-tt", action="store_true")
    a = ap.parse_args()
    if a.refresh:
        print(refresh())
    elif a.load_keith:
        print("stored", load_keith_csv(a.load_keith))
    elif a.load_tt:
        print("stored", load_trend_tail())
    else:
        for t, r in lookup([x.upper() for x in a.lookup]).items():
            fmt = lambda x: f"{x[0]:,.2f} ({x[1]}; {x[2]})" if x[0] is not None else "none"
            # 11/14 trap (weekend dry run): one "TREND" printed the cloud TOP for longs. The card uses
            # the BOTTOM edge for longs and the TOP edge for shorts, so print both, labelled by side.
            print(f"{t}{' via ' + r['proxy'] if r['proxy'] else ''}: TRADE {fmt(r['trade'])} · "
                  f"TREND for LONGS {fmt(r['trend_long'])} · TREND for SHORTS {fmt(r['trend_short'])}"
                  + (f" · TAIL {r['tail']:,.2f}" if r["tail"] else "")
                  + (f" · ETF Pro {r['etf_pro'][0] or 0:g}-{r['etf_pro'][1] or 0:g} {r['etf_pro'][2]} ({r['etf_pro'][3]})" if r["etf_pro"] else "")
                  + (f" · Risk Range {r['risk_range'][0]:g}-{r['risk_range'][1]:g} {r['risk_range'][2]} ({r['risk_range'][3]})" if r["risk_range"] else "")
                  + (f" · lines through {r['line_bar']}" if r.get("line_bar") else "")
                  + (f" · EMA closes through {r['ema_bar']}" if r.get("ema_bar") and r.get("ema_bar") != r.get("line_bar") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
