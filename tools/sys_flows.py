"""tools/sys_flows.py - our own vol-control and CTA tracker (Kris 2026-11-26).

"is there a way we can track the vol control funds and CTA other than Tier 1 Alpha"

Nobody sees these flows; Tier 1 Alpha models them from prices. So do we, from Yahoo daily bars.

VOL CONTROL  (S&P 500)
  rv_1m   = 20-bar realized vol, zero mean, annualized   (matches T1A's 1M within ~0.4 pts)
  rv_3m   = 62-bar realized vol, annualized              (matches T1A's 3M within ~0.1 pts)
  rv_used = the higher of the two: the funds size on it
  notional_bn = K / rv_used    K is fitted to T1A's own "vol control notional" history
                               (their number x their vol is a constant, ~2,500)
  flow_by_move = notional after the NEXT bar moves -5..+5 %, minus notional today ($bn)
  dropoff      = the returns that leave the 1M / 3M windows over the next 10 bars.
                 A big day leaving = vol falls = they BUY, with no price move needed.
  exp_t10/12/15 = equity weight of a plain 10 / 12 / 15 % target fund (S&P Risk Control rule,
                  capped at 150 %).

CTA TREND  (every market in tools/sys_flows_markets.py: stocks, bonds, currencies, commodities, bitcoin)
  Four lookbacks: 21, 63, 126, 252 bars. Signal = +1 if the close is above the close N bars
  ago, else -1. score = weighted average x 100 (-100 full short ... +100 full long). The weights
  are the blend that best matches real managed-futures fund returns (tools/sys_flows_fit.py
  --weights); equal weights until that has been run.
  levels  = for the NEXT bar, the close each lookback will be compared with. A close through
            one flips that signal: next_sell is the nearest one below, next_buy the nearest above
            (only lookbacks that carry weight).
  Stocks use the cash index or ETF. Bonds and commodities use Yahoo's front-month future, which
  jumps at each roll: read those levels as approximate.

HARD DATA NEXT TO THE MODEL  (four reads per market, one row)
  funds  : printed daily futures books of DBMF, KMLM and Simplify CTA (tools/sys_flows_funds.py)
  fit    : exposures solved from a dozen funds' real daily returns (tools/sys_flows_fit.py)
  CFTC   : the regulator's weekly count, leveraged funds / managed money (tools/sys_flows_cot.py)
  read   : LONG / SHORT n/n when every available read points the same way, else MIXED.
  The follow-through test (does price keep going after a flip?) prints at the bottom.

Check against T1A: every run prints how close our numbers are to theirs on the days we have both.

Usage: py -m tools.sys_flows [--days 120] [--no-save]
Writes sys_flow_volcontrol, sys_flow_cta, sys_flow_fund_holdings, sys_flow_cot, sys_flow_fit,
data/sys_flows/<fund>_<date>.csv and outputs/sys_flows/sys_flows_<date>.txt.
Read-only on everything else. No Telegram: the desk reads it at check-ins.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")

SPX = "^GSPC"
N_1M, N_3M = 20, 62
K_DEFAULT = 2500.0
MOVES = list(range(-5, 6))
TARGETS = (10, 12, 15)
LOOKBACKS = (21, 63, 126, 252)

from tools.sys_flows_markets import BY_KEY, CLASSES, MARKETS   # noqa: E402

TREND_MARKETS = [m for m in MARKETS if m.get("yahoo")]


def _closes(symbols):
    import pandas as pd
    import yfinance as yf
    px = yf.download(sorted(set(symbols)), period="2y", interval="1d", auto_adjust=False,
                     progress=False, group_by="column", threads=True)
    if px is None or px.empty:
        return pd.DataFrame()
    c = px["Close"]
    if isinstance(c, pd.Series):
        c = c.to_frame(symbols[0])
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c


def _conn():
    import db_pg
    import psycopg2
    db_pg._load_dotenv_fallback()
    return psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn())


# ── vol control ──────────────────────────────────────────────────────────────

def _rv(rets, n, zero_mean):
    """Annualized realized vol in % from the last n log returns."""
    import numpy as np
    w = np.asarray(rets[-n:], dtype=float)
    if len(w) < n:
        return None
    var = (w ** 2).mean() if zero_mean else w.var(ddof=1)
    return float(np.sqrt(var * 252) * 100)


def _rv_pair(rets):
    return _rv(rets, N_1M, True), _rv(rets, N_3M, False)


def vol_control_rows(spx, k, days):
    """One dict per bar for the last `days` bars. spx = Series of closes."""
    import numpy as np
    spx = spx.dropna()
    r = np.log(spx).diff().dropna()
    rl = list(r.values)
    idx = list(r.index)
    rows = []
    first = max(N_3M, len(rl) - days)
    prev_notional = None
    for i in range(first - 1, len(rl)):
        hist = rl[: i + 1]
        rv1, rv3 = _rv_pair(hist)
        if rv1 is None or rv3 is None:
            continue
        used = max(rv1, rv3)
        notional = k / used
        flows = {}
        for m in MOVES:
            a, b = _rv_pair(hist + [float(np.log(1 + m / 100))])
            flows[str(m)] = round(k / max(a, b) - notional, 2)
        drop = []
        for t in range(1, 11):          # the return leaving each window t bars from now
            j1, j3 = i + 1 - N_1M + (t - 1), i + 1 - N_3M + (t - 1)
            if j1 > i or j3 < 0:
                continue
            drop.append({"t": t, "d1m": str(idx[j1].date()), "drop1m": round(100 * (np.exp(rl[j1]) - 1), 2),
                         "d3m": str(idx[j3].date()), "drop3m": round(100 * (np.exp(rl[j3]) - 1), 2)})
        row = {"bar_date": idx[i].date(), "spx_close": float(spx.loc[idx[i]]), "rv_1m": round(rv1, 2),
               "rv_3m": round(rv3, 2), "rv_used": round(used, 2), "notional_bn": round(notional, 1),
               "notional_chg_bn": None if prev_notional is None else round(notional - prev_notional, 1),
               "flow_by_move": flows, "dropoff": drop, "k_fit": round(k, 1)}
        for t in TARGETS:
            row[f"exp_t{t}"] = round(min(150.0, 100.0 * t / used), 1)
        prev_notional = notional
        rows.append(row)
    return rows[1:] if len(rows) > 1 else rows   # the first row has no change to show


def fit_k(t1a):
    """K = median of (their notional x their higher vol). Falls back to 2,500."""
    ks = [float(n) * max(float(a), float(b)) for _, n, a, b, *_ in t1a
          if n is not None and a is not None and b is not None]
    return statistics.median(ks) if len(ks) >= 5 else K_DEFAULT


# ── CTA trend ────────────────────────────────────────────────────────────────

def lookback_w():
    """{lookback: weight} summing to 1. From the fitted blend if present, else equal."""
    from tools.sys_flows_fit import load_weights
    w = load_weights()
    raw = {n: float((w or {}).get("weights", {}).get(str(n), 0.0)) for n in LOOKBACKS}
    tot = sum(raw.values())
    return {n: v / tot for n, v in raw.items()} if tot > 0 else {n: 1.0 / len(LOOKBACKS) for n in LOOKBACKS}


def cta_rows(closes, days, weights=None):
    weights = weights or lookback_w()
    live = [n for n in LOOKBACKS if weights[n] > 0]
    rows = []
    for m in TREND_MARKETS:
        sym = m["yahoo"]
        if sym not in closes:
            continue
        s = closes[sym].dropna()
        if len(s) < max(LOOKBACKS) + 2:
            continue
        v = list(s.values)
        idx = list(s.index)
        for i in range(max(max(LOOKBACKS), len(v) - days), len(v)):
            px = float(v[i])
            sig = {str(n): (1 if px > v[i - n] else -1) for n in LOOKBACKS}
            lev = {str(n): round(float(v[i - n + 1]), 6) for n in LOOKBACKS}   # next bar's reference
            below = [lev[str(n)] for n in live if lev[str(n)] < px]
            above = [lev[str(n)] for n in live if lev[str(n)] > px]
            rows.append({"bar_date": idx[i].date(), "symbol": sym, "label": m["label"], "asset_class": m["cls"],
                         "market": m["key"], "close": round(px, 6),
                         "score": round(100.0 * sum(weights[n] * sig[str(n)] for n in LOOKBACKS), 1),
                         "signals": sig, "levels": lev,
                         "next_sell": max(below) if below else None,
                         "next_buy": min(above) if above else None})
    return rows


# ── T1A check ────────────────────────────────────────────────────────────────

def load_t1a(conn):
    cur = conn.cursor()
    cur.execute("""SELECT data_date, vc_notional_bn, rv_1m_t1a, rv_3m_t1a, cta_position, vc_flow_by_move
                   FROM t1a_snapshots WHERE data_date IS NOT NULL ORDER BY data_date""")
    return cur.fetchall()


def _corr(a, b):
    import numpy as np
    if len(a) < 5:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def t1a_check(t1a, vc, cta):
    """Lines saying how close we are to T1A on the bars both have."""
    by_date = {r["bar_date"]: r for r in vc}
    spx_score = {r["bar_date"]: r["score"] for r in cta if r["symbol"] == SPX}
    e1, e3, en, ours_n, their_n, ours_c, their_c, ef = [], [], [], [], [], [], [], []
    for d, n, a, b, c, flows in t1a:
        o = by_date.get(d)
        if o:
            if a is not None:
                e1.append(abs(o["rv_1m"] - float(a)))
            if b is not None:
                e3.append(abs(o["rv_3m"] - float(b)))
            if n is not None:
                en.append(abs(o["notional_bn"] - float(n)))
                ours_n.append(o["notional_bn"])
                their_n.append(float(n))
            if flows:
                ef += [abs(o["flow_by_move"][m] - float(flows[m])) for m in flows if m in o["flow_by_move"]]
        if c is not None and d in spx_score:
            ours_c.append(spx_score[d])
            their_c.append(float(c))
    out = []
    if en:
        out.append(f"  vol control, {len(en)} shared days: our $bn is off by {statistics.mean(en):.1f} on average "
                   f"(correlation {_corr(ours_n, their_n):.2f}); 1M vol off {statistics.mean(e1):.2f} pts, "
                   f"3M vol off {statistics.mean(e3):.2f} pts")
    if ef:
        out.append(f"  flow-by-move table: off by ${statistics.mean(ef):.1f}bn per cell on average")
    if len(ours_c) >= 5:
        out.append(f"  CTA, {len(ours_c)} shared days: our S&P trend score vs their CTA index, "
                   f"correlation {_corr(ours_c, their_c):.2f} (different scales: compare direction, not level)")
    return out or ["  no shared days with Tier 1 Alpha yet"]


# ── report ───────────────────────────────────────────────────────────────────

def _px(x):
    if x is None:
        return "-"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}" if abs(x) >= 10 else f"{x:.4f}"


def _latest(cta):
    """({market: last row}, {market: row before it})."""
    last, prev = {}, {}
    for r in sorted(cta, key=lambda r: r["bar_date"]):
        k = r["market"]
        if k in last:
            prev[k] = last[k]
        last[k] = r
    return last, prev


def _read(score, fund_w, fit, cot, cls):
    """LONG / SHORT n/n when every read that has a view points one way, else MIXED."""
    signs = []
    if score is not None and abs(score) >= 20:
        signs.append(1 if score > 0 else -1)
    for w in (fund_w or {}).values():                    # each fund's printed book is its own read
        if abs(w) >= 2:
            signs.append(1 if w > 0 else -1)
    if fit is not None and abs(fit) >= 5:
        signs.append(1 if fit > 0 else -1)
    if cot and cls == "commodity" and cot.get("pct_oi") is not None and abs(cot["pct_oi"]) >= 3:
        signs.append(1 if cot["net"] > 0 else -1)       # financial futures: level is muddied by basis trades
    if len(signs) < 2:
        return ""
    if all(x > 0 for x in signs):
        return f"LONG {len(signs)}/{len(signs)}"
    if all(x < 0 for x in signs):
        return f"SHORT {len(signs)}/{len(signs)}"
    return "MIXED"


def _cell(v, width, fmt="{:+.0f}"):
    return f"{'' if v is None else fmt.format(v):>{width}}"


def positions_lines(cta, hold, fit, cot):
    """The all-assets table: trend score, three funds' books, returns fit, CFTC, and whether they agree."""
    from tools.sys_flows_funds import FUNDS
    last, _ = _latest(cta)
    pos, chg, dates, notes = hold
    now_fit = (fit or {}).get("now") or {}
    b_now = now_fit.get("beta", {})
    b_prev = ((fit or {}).get("prev") or {}).get("beta", {})
    cot_date = max((c["date"] for c in cot.values()), default=None)
    L = ["", "POSITIONS, ALL ASSETS  (four independent reads per market)",
         "  trend = our score -100..+100 | funds = printed futures book, % of fund | fit = solved from real fund",
         "  returns, % of fund (5d = change in 5 bars) | CFTC = net % of open interest, week change in contracts,",
         "  rank in 3 years (0 most short, 100 most long)",
         "  fund holdings dates: " + (", ".join(f"{f} {dates[f]}" for f in FUNDS if f in dates) or "none")
         + f" | fit {now_fit.get('bar_date', '-')}, r2 {now_fit.get('r2', 0):.2f} | CFTC {cot_date or '-'}",
         f"  {'market':<18}{'trend':>6} |{'DBMF':>6}{'KMLM':>6}{'CTA':>6} |{'fit':>5}{'5d':>5} |"
         f"{'%OI':>6}{'wk chg':>9}{'rank':>5} | read"]
    for cls in CLASSES:
        rows = []
        for m in [m for m in MARKETS if m["cls"] == cls]:
            k = m["key"]
            sc = last[k]["score"] if k in last else None
            fw, c = pos.get(k, {}), cot.get(k)
            ft = b_now.get(k)
            if sc is None and not fw and ft is None and not c:
                continue
            d5 = None if ft is None or k not in b_prev else ft - b_prev[k]
            if d5 is not None and abs(d5) < 3:
                d5 = None
            if c and c.get("pct_oi") is not None and c.get("chg1w") is not None:
                cot_txt = _cell(c["pct_oi"], 6) + _cell(c["chg1w"], 9, "{:+,}") + _cell(c["pctile"], 5, "{}")
            else:
                cot_txt = " " * 20
            rows.append(f"  {m['label']:<18}{_cell(sc, 6)} |" + "".join(_cell(fw.get(f), 6) for f in FUNDS) + " |"
                        + _cell(ft, 5) + _cell(d5, 5) + " |" + cot_txt + f" | {_read(sc, fw, ft, c, cls)}")
        if rows:
            L += [f"  -- {cls}"] + rows
    moves = []
    for k, d in chg.items():
        for f, v in d.items():
            moves.append((abs(v), f"{BY_KEY[k]['label']} {f} {v:+.0f}"))
    if moves:
        L.append("  fund changes since their prior holdings date (actual buying / selling, % of fund): "
                 + ", ".join(t for _, t in sorted(moves, reverse=True)[:14]))
    else:
        L.append("  fund changes: none yet (they show from each fund's second captured holdings date)")
    L += [f"  note - {n}" for n in notes]
    return L


def trend_lines(cta, weights):
    last, prev = _latest(cta)
    live = [n for n in LOOKBACKS if weights[n] > 0]
    L = ["", "CTA TREND LEVELS  (signals 1m 3m 6m 12m; weights "
         + " ".join(f"{n}d {weights[n] * 100:.0f}%" for n in LOOKBACKS) + ")",
         "  a close below 'sell below' turns one weighted signal short; above 'buy above' turns one long",
         f"  {'market':<18}{'close':>10} {'score':>6} {'chg':>5}  sig   {'sell below':>11} {'buy above':>11}"]
    for cls in CLASSES:
        L.append(f"  -- {cls}")
        for m in [m for m in TREND_MARKETS if m["cls"] == cls]:
            r = last.get(m["key"])
            if not r:
                continue
            p = prev.get(m["key"])
            chg = "" if not p or abs(p["score"] - r["score"]) < 0.5 else f"{r['score'] - p['score']:+.0f}"
            sig = "".join("+" if r["signals"][str(n)] > 0 else "-" for n in LOOKBACKS)
            L.append(f"  {m['label']:<18}{_px(r['close']):>10} {r['score']:>+6.0f} {chg:>5}  {sig}  "
                     f"{_px(r['next_sell']):>11} {_px(r['next_buy']):>11}" + (" ~" if m.get("roll") else ""))
    L.append("  ~ = front-month future, levels approximate across contract rolls")
    if len(live) < len(LOOKBACKS):
        L.append("  lookbacks with 0% weight are shown in sig but do not move the score or the levels")
    return L


def checks_lines(t1a_lines, fit, hold, weights_meta):
    L = ["", "CHECKS", "  vs Tier 1 Alpha:"] + ["  " + x for x in t1a_lines]
    d = (fit or {}).get("dbmf")
    book = hold[0]
    if d:
        pairs = [(k, v, book[k]["DBMF"]) for k, v in d["beta"].items() if "DBMF" in book.get(k, {})]
        if pairs:
            ok = [p for p in pairs if (p[1] > 0) == (p[2] > 0) or abs(p[1]) < 5 or abs(p[2]) < 5]
            bad = [p for p in pairs if p not in ok]
            L.append(f"  returns fit vs DBMF's printed book: same side on {len(ok)} of {len(pairs)} markets, "
                     f"average miss {sum(abs(p[1] - p[2]) for p in ok) / max(1, len(ok)):.0f} pts (fit r2 {d['r2']:.2f})")
            for k, v, w in bad:
                L.append(f"    MISMATCH {BY_KEY[k]['label']}: its returns behave {v:+.0f}%, its page prints {w:+.0f}%. "
                         "Treat that printed line as unverified.")
    if weights_meta:
        L.append(f"  lookback blend fitted on {weights_meta['days']} days of real fund returns "
                 f"({weights_meta['from']} to {weights_meta['to']}): correlation {weights_meta['corr']:.2f} with the funds")
    else:
        L.append("  lookback blend: equal weights (not fitted yet: py -m tools.sys_flows_fit --weights)")
    return L


def build_report(vc, now, sections):
    L = [f"SYSTEMATIC FLOWS (our model + hard data)  run {now:%Y-%m-%d %H:%M} ET"]
    if vc:
        v = vc[-1]
        wk = vc[-6]["notional_bn"] if len(vc) >= 6 else None
        L += ["", f"VOL CONTROL  S&P {v['spx_close']:,.0f}  bar {v['bar_date']}",
              f"  realized vol 1M {v['rv_1m']:.1f}  3M {v['rv_3m']:.1f}  -> sizing on {v['rv_used']:.1f}",
              f"  exposure ${v['notional_bn']:.0f}bn  "
              f"day {v['notional_chg_bn'] if v['notional_chg_bn'] is not None else 0:+.1f}bn"
              + (f"  5 days {v['notional_bn'] - wk:+.1f}bn" if wk is not None else ""),
              "  plain target funds: " + "  ".join(f"{t}% target = {v[f'exp_t{t}']:.0f}% stocks" for t in TARGETS),
              "  if the next bar moves (flow $bn, + buy / - sell):",
              "    " + "  ".join(f"{m:+d}%: {v['flow_by_move'][str(m)]:+.1f}" for m in MOVES if m != 0)
              + f"   flat: {v['flow_by_move']['0']:+.1f}"]
        big = [d for d in v["dropoff"] if abs(d["drop1m"]) >= 1.5]
        if big:
            L.append("  big days leaving the 1M window (vol falls -> they buy): "
                     + ", ".join(f"in {d['t']} bars {d['drop1m']:+.1f}% ({d['d1m'][5:]})" for d in big))
        else:
            L.append("  no day of 1.5%+ leaves the 1M window in the next 10 bars")
    for sec in sections:
        L += sec
    return "\n".join(L)


# ── save ─────────────────────────────────────────────────────────────────────

def save(conn, vc, cta):
    cur = conn.cursor()
    for r in vc:
        cur.execute("""INSERT INTO sys_flow_volcontrol (bar_date, spx_close, rv_1m, rv_3m, rv_used, notional_bn,
                         notional_chg_bn, exp_t10, exp_t12, exp_t15, flow_by_move, dropoff, k_fit, computed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                       ON CONFLICT (bar_date) DO UPDATE SET spx_close=EXCLUDED.spx_close, rv_1m=EXCLUDED.rv_1m,
                         rv_3m=EXCLUDED.rv_3m, rv_used=EXCLUDED.rv_used, notional_bn=EXCLUDED.notional_bn,
                         notional_chg_bn=EXCLUDED.notional_chg_bn, exp_t10=EXCLUDED.exp_t10, exp_t12=EXCLUDED.exp_t12,
                         exp_t15=EXCLUDED.exp_t15, flow_by_move=EXCLUDED.flow_by_move, dropoff=EXCLUDED.dropoff,
                         k_fit=EXCLUDED.k_fit, computed_at=now()""",
                    (r["bar_date"], r["spx_close"], r["rv_1m"], r["rv_3m"], r["rv_used"], r["notional_bn"],
                     r["notional_chg_bn"], r["exp_t10"], r["exp_t12"], r["exp_t15"],
                     json.dumps(r["flow_by_move"]), json.dumps(r["dropoff"]), r["k_fit"]))
    from psycopg2.extras import execute_values
    execute_values(cur, """INSERT INTO sys_flow_cta (bar_date, symbol, label, asset_class, close, score, signals, levels,
                         next_sell, next_buy, computed_at) VALUES %s
                       ON CONFLICT (bar_date, symbol) DO UPDATE SET label=EXCLUDED.label, close=EXCLUDED.close,
                         score=EXCLUDED.score, signals=EXCLUDED.signals, levels=EXCLUDED.levels,
                         next_sell=EXCLUDED.next_sell, next_buy=EXCLUDED.next_buy, computed_at=now()""",
                   [(r["bar_date"], r["symbol"], r["label"], r["asset_class"], r["close"], r["score"],
                     json.dumps(r["signals"]), json.dumps(r["levels"]), r["next_sell"], r["next_buy"]) for r in cta],
                   template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())", page_size=1000)
    conn.commit()


def _try(label, fn, default):
    try:
        return fn()
    except Exception as e:
        print(f"sys_flows: {label} failed ({str(e)[:100]})")
        return default


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120, help="bars of history to (re)write")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)
    now = datetime.now(ET).replace(tzinfo=None)
    from tools import sys_flows_cot as cot_mod
    from tools import sys_flows_fit as fit_mod
    from tools import sys_flows_funds as funds_mod

    closes = _closes([m["yahoo"] for m in TREND_MARKETS])
    if closes.empty or SPX not in closes:
        print("sys_flows: FAILED - no price data from Yahoo")
        return 1
    conn = _conn()
    t1a = load_t1a(conn)
    vc = vol_control_rows(closes[SPX], fit_k(t1a), a.days)
    weights = lookback_w()
    cta = cta_rows(closes, a.days, weights)

    by_fund, errors = _try("fund holdings", funds_mod.fetch_all, ({}, {}))
    for f, e in errors.items():
        print(f"sys_flows: {f} holdings failed ({e})")
    hold = funds_mod.positions(conn, by_fund)
    cot_rows = _try("CFTC", cot_mod.fetch, [])
    fit = _try("returns fit", fit_mod.daily_fit, {})

    spx_rows = [dict(r, symbol=SPX) for r in cta if r["market"] == "spx"]
    sections = [positions_lines(cta, hold, fit, cot_mod.summary(cot_rows)),
                trend_lines(cta, weights),
                ["", "FOLLOW-THROUGH TEST  (does price keep going after a flip?)"]
                + fit_mod.study_lines(fit_mod.load_study()),
                checks_lines(t1a_check(t1a, vc, spx_rows), fit, hold, fit_mod.load_weights())]
    report = build_report(vc, now, sections)
    print(report)
    if not a.no_save:
        save(conn, vc, cta)
        funds_mod.save(conn, by_fund)
        if cot_rows:
            cot_mod.save(conn, cot_rows)
        fit_mod.save_fit(conn, fit)
        os.makedirs("outputs/sys_flows", exist_ok=True)
        path = f"outputs/sys_flows/sys_flows_{now:%Y-%m-%d}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nsys_flows: saved {len(vc)} vol-control bars, {len(cta)} trend rows, "
              f"{sum(len(v) for v in by_fund.values())} fund lines, {len(cot_rows)} CFTC rows, {path}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
