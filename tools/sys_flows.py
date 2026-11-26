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

CTA TREND  (13 markets)
  Four lookbacks: 21, 63, 126, 252 bars. Signal = +1 if the close is above the close N bars
  ago, else -1. score = average x 100 (-100 full short ... +100 full long).
  levels  = for the NEXT bar, the close each lookback will be compared with. A close through
            one flips that signal: next_sell is the nearest one below, next_buy the nearest above.
  Equities use the cash index (no roll gap). Bonds, oil, gold, copper use Yahoo's front-month
  future, which jumps at each roll: read those levels as approximate.

Check against T1A: every run prints how close our numbers are to theirs on the days we have both.

Usage: py -m tools.sys_flows [--days 120] [--no-save]
Writes sys_flow_volcontrol, sys_flow_cta (upsert per bar) and outputs/sys_flows/sys_flows_<date>.txt.
Read-only on everything else. No Telegram: the desk reads it at check-ins.
"""
from __future__ import annotations

import argparse
import json
import os
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

# (label, yahoo symbol, asset class)
CTA_UNIVERSE = [
    ("S&P 500", "^GSPC", "equity"), ("Nasdaq 100", "^NDX", "equity"),
    ("Russell 2000", "^RUT", "equity"), ("Euro Stoxx 50", "^STOXX50E", "equity"),
    ("Japan Nikkei", "^N225", "equity"),
    ("UST 10y future", "ZN=F", "bond"), ("UST 30y future", "ZB=F", "bond"),
    ("Dollar DXY", "DX-Y.NYB", "fx"), ("EUR/USD", "EURUSD=X", "fx"), ("USD/JPY", "JPY=X", "fx"),
    ("WTI crude", "CL=F", "commodity"), ("Gold", "GC=F", "commodity"),
    ("Copper", "HG=F", "commodity"),
]
ROLL_GAP = {"ZN=F", "ZB=F", "CL=F", "GC=F", "HG=F"}


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

def cta_rows(closes, days):
    rows = []
    for label, sym, cls in CTA_UNIVERSE:
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
            lev = {str(n): round(float(v[i - n + 1]), 4) for n in LOOKBACKS}   # next bar's reference
            below = [x for x in lev.values() if x < px]
            above = [x for x in lev.values() if x > px]
            rows.append({"bar_date": idx[i].date(), "symbol": sym, "label": label, "asset_class": cls,
                         "close": round(px, 4), "score": 100.0 * sum(sig.values()) / len(LOOKBACKS),
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


def build_report(vc, cta, check, now):
    L = [f"SYSTEMATIC FLOWS (our model)  run {now:%Y-%m-%d %H:%M} ET"]
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
    last = {}
    for r in cta:
        if r["symbol"] not in last or r["bar_date"] >= last[r["symbol"]]["bar_date"]:
            last[r["symbol"]] = r
    prev = {}
    for r in cta:
        if r["bar_date"] < last[r["symbol"]]["bar_date"]:
            if r["symbol"] not in prev or r["bar_date"] > prev[r["symbol"]]["bar_date"]:
                prev[r["symbol"]] = r
    if last:
        L += ["", "CTA TREND  score -100 short .. +100 long  (1m 3m 6m 12m signals)",
              f"  {'market':<16}{'close':>10} {'score':>6} {'chg':>5}  sig   {'sell below':>11} {'buy above':>11}"]
        for label, sym, _ in CTA_UNIVERSE:
            r = last.get(sym)
            if not r:
                continue
            p = prev.get(sym)
            chg = "" if not p or p["score"] == r["score"] else f"{r['score'] - p['score']:+.0f}"
            sig = "".join("+" if r["signals"][str(n)] > 0 else "-" for n in LOOKBACKS)
            L.append(f"  {label:<16}{_px(r['close']):>10} {r['score']:>+6.0f} {chg:>5}  {sig}  "
                     f"{_px(r['next_sell']):>11} {_px(r['next_buy']):>11}" + (" ~" if sym in ROLL_GAP else ""))
        L.append("  ~ = front-month future, levels approximate across contract rolls")
    L += ["", "CHECK vs Tier 1 Alpha"] + check
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
    for r in cta:
        cur.execute("""INSERT INTO sys_flow_cta (bar_date, symbol, label, asset_class, close, score, signals, levels,
                         next_sell, next_buy, computed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                       ON CONFLICT (bar_date, symbol) DO UPDATE SET close=EXCLUDED.close, score=EXCLUDED.score,
                         signals=EXCLUDED.signals, levels=EXCLUDED.levels, next_sell=EXCLUDED.next_sell,
                         next_buy=EXCLUDED.next_buy, computed_at=now()""",
                    (r["bar_date"], r["symbol"], r["label"], r["asset_class"], r["close"], r["score"],
                     json.dumps(r["signals"]), json.dumps(r["levels"]), r["next_sell"], r["next_buy"]))
    conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120, help="bars of history to (re)write")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)
    now = datetime.now(ET).replace(tzinfo=None)

    closes = _closes([s for _, s, _ in CTA_UNIVERSE])
    if closes.empty or SPX not in closes:
        print("sys_flows: FAILED - no price data from Yahoo")
        return 1
    conn = _conn()
    t1a = load_t1a(conn)
    k = fit_k(t1a)
    vc = vol_control_rows(closes[SPX], k, a.days)
    cta = cta_rows(closes, a.days)
    report = build_report(vc, cta, t1a_check(t1a, vc, cta), now)
    print(report)
    if not a.no_save:
        save(conn, vc, cta)
        os.makedirs("outputs/sys_flows", exist_ok=True)
        path = f"outputs/sys_flows/sys_flows_{now:%Y-%m-%d}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\nsys_flows: saved {len(vc)} vol-control bars, {len(cta)} CTA rows, {path}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
