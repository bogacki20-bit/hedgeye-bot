"""eod_report.py — end-of-day performance and exposure report (Kris 11/21/2026).

Read-only. Prints the report and saves it to outputs/eod_report/<date>.txt.

  1. P&L per account: today, last 5 sessions, month to date, vs SPY
  2. By side: longs, shorts (direct + inverse ETFs), cash parking; best and worst names
  3. Exposure per account: long, short, net, gross, cash, and net adjusted for each name's
     60-day beta to SPY. Information only: there is no exposure target (Kris 11/21).
  4. What-if: SPX -3% and the 10-year +25 bp, from each name's 60-day betas to SPY and TLT
  5. Conviction log tally: calls by grade, taken or not, outcomes filled

How the numbers are built (so nobody over-trusts them):
  - book_positions snapshots are downloaded in the morning, so the snapshot dated D is the
    book at the close of the session before D.
  - P&L is HOLDINGS-BASED: for each session, the morning's positions x that session's price
    change (dividends included). It ignores that day's trades, so intraday trading gains
    and losses are not in it. It needs no deposit or withdrawal adjustment.
  - Account value = the sum of the snapshot's market values. Fidelity's cash line lags
    unsettled trades, so that value can be off by a few percent on a heavy trading day;
    the % figures use it as the denominator.

  py -m tools.eod_report            # print + save
  py -m tools.eod_report --no-save
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "outputs", "eod_report")
CONV = os.path.join(ROOT, "data", "stalk", "conviction_log.csv")

ACCOUNTS = ["Individual", "Rollover IRA", "ROTH IRA"]
CASH = {"SPAXX", "FDRXX", "CORE", "QPCTQ", "BUXX"}          # cash parking: not exposure
INVERSE = {"NVDD": 1, "METD": 1, "ORCS": 1, "PSQ": 1, "SH": 1, "RWM": 1, "MSFD": 1, "TSLQ": 1, "SKYD": 1,
           "SCO": 2, "DRIP": 2, "ERY": 2, "ZSL": 2}         # held long = short exposure, x leverage
NO_PRICE = {"SKYDWS"}                                        # no price source
SPX_SHOCK = -0.03
TLT_PER_25BP = -0.042                                        # TLT duration ~17 -> about -4.2% for +25 bp
BETA_DAYS = 60


def _sym(s: str) -> str:
    return s.replace("*", "").strip().upper()


def load():
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select snapshot_date, account_name, symbol, quantity, last_price, market_value, cost_basis
                       from book_positions
                       where snapshot_date >= current_date - 50 and account_name = any(%s) and not coalesce(is_option, false)
                       order by snapshot_date""", (ACCOUNTS,))
        snaps = defaultdict(lambda: defaultdict(list))
        for d, a, s, q, lp, mv, cb in cur.fetchall():
            snaps[d][a].append(dict(sym=_sym(s), qty=float(q or 0), px=float(lp) if lp is not None else None,
                                    mv=float(mv or 0), cost=float(cb) if cb is not None else None))
    return snaps


def nav(rows) -> float:
    return sum(r["mv"] for r in rows)


def side_of(r) -> str:
    s = r["sym"]
    return "cash parking" if s in CASH else "shorts + inverse ETFs" if r["qty"] < 0 or s in INVERSE else "longs"


def prices(symbols):
    import numpy as np
    import yfinance as yf
    syms = sorted({s for s in symbols if s not in NO_PRICE} | {"SPY", "TLT"})
    d = yf.download(syms, period="6mo", interval="1d", progress=False, threads=True, group_by="ticker", auto_adjust=True)
    close = {}
    for s in syms:
        try:
            c = d[s]["Close"].dropna()
        except Exception:
            continue
        if len(c) >= 30:
            close[s] = c
    spy, tlt = close["SPY"].pct_change().dropna(), close["TLT"].pct_change().dropna()
    betas = {}
    for s, c in close.items():
        r = c.pct_change().dropna()
        idx = r.index.intersection(spy.index).intersection(tlt.index)[-BETA_DAYS:]
        if len(idx) < 30:
            continue
        X = np.column_stack([np.ones(len(idx)), spy.loc[idx].values, tlt.loc[idx].values])
        b = np.linalg.lstsq(X, r.loc[idx].values, rcond=None)[0]
        b1 = float(np.cov(r.loc[idx].values, spy.loc[idx].values)[0, 1] / np.var(spy.loc[idx].values, ddof=1))
        betas[s] = dict(spy=b1, spy2=float(b[1]), tlt2=float(b[2]))
    return close, betas


def session_pnl(snaps, close, n_sessions=30):
    """Holdings-based P&L per session: [(session, snapshot_date, {acct: rec})].
    rec has nav, pnl, one total per side, and names = [(pnl, sym)]."""
    dates = sorted(snaps)
    sess = [d.date() for d in close["SPY"].index]
    last = {s: {d.date(): float(v) for d, v in c.items()} for s, c in close.items()}
    out = []
    for i in range(max(1, len(sess) - n_sessions), len(sess)):
        S, P = sess[i], sess[i - 1]
        snap = max((d for d in dates if d <= S), default=None)
        if snap is None or (S - snap).days > 4:
            continue
        day = {}
        for a in ACCOUNTS:
            rows = snaps[snap].get(a, [])
            rec = defaultdict(float)
            rec["nav"] = nav(rows)
            names = []
            for r in rows:
                px = last.get(r["sym"], {})
                if not r["qty"] or S not in px or P not in px:
                    continue
                pl = r["qty"] * (px[S] - px[P])
                rec["pnl"] += pl
                rec[side_of(r)] += pl
                names.append((pl, r["sym"]))
            rec["names"] = names
            day[a] = rec
        out.append((S, snap, day))
    return out


def chain(pairs) -> float | None:
    """pairs = [(pnl, nav)] -> compounded return."""
    x, used = 1.0, 0
    for pl, n in pairs:
        if n > 0:
            x *= 1 + pl / n
            used += 1
    return x - 1 if used else None


def pct(x, w=7) -> str:
    return f"{'n/a':>{w}}" if x is None else f"{x * 100:>+{w - 1}.2f}%"


def usd(x, w=8) -> str:
    return f"{'n/a':>{w}}" if x is None else f"{x:>+{w},.0f}"


def build() -> str:
    snaps = load()
    if not snaps:
        return "EOD REPORT: no book snapshots in the last 50 days"
    dates = sorted(snaps)
    last = dates[-1]
    book = snaps[last]
    symbols = {r["sym"] for d in dates for a in ACCOUNTS for r in snaps[d].get(a, [])
               if r["sym"] not in CASH or r["sym"] == "BUXX"}
    close, betas = prices(symbols)
    bar = close["SPY"].index[-1].date()
    sp = session_pnl(snaps, close)
    L = [f"EOD REPORT | session {bar} | book = the {last} morning snapshot | built {dt.datetime.now():%Y-%m-%d %H:%M}"]
    if not sp or sp[-1][0] != bar:
        L.append("NOTE: no usable snapshot for the latest session; P&L below stops earlier.")
    held = {r["sym"] for a in ACCOUNTS for r in book.get(a, []) if r["qty"] and r["sym"] not in CASH}
    missing = {s for s in held if s not in close}
    for a in ACCOUNTS:                      # value each position at the latest close for the exposure tables
        for r in book.get(a, []):
            r["now"] = r["mv"]
            if r["sym"] in close and r["qty"] and r["px"] and bar >= last:
                r["now"] = r["qty"] * float(close[r["sym"]].iloc[-1])

    spy_day = {d.date(): float(v) for d, v in close["SPY"].pct_change().dropna().items()}

    def spy_chain(sessions):
        x = 1.0
        for S in sessions:
            x *= 1 + spy_day.get(S, 0.0)
        return x - 1 if sessions else None

    s5 = sp[-5:]
    sm = [x for x in sp if x[0].month == bar.month and x[0].year == bar.year]
    today = sp[-1][2] if sp and sp[-1][0] == bar else {}

    # 1. P&L
    L += ["", "1. PROFIT AND LOSS (holdings-based: the morning's positions x the day's price move; that day's trades not included)",
          f"{'account':14}{'value':>10}{'today $':>10}{'today':>9}{'5 sess $':>10}{'5 sess':>9}{'MTD $':>10}{'MTD':>9}"]
    tot = defaultdict(float)
    for a in ACCOUNTS:
        n0 = nav(book.get(a, []))
        t = today.get(a, {}).get("pnl")
        f5 = sum(x[2][a]["pnl"] for x in s5)
        fm = sum(x[2][a]["pnl"] for x in sm)
        L.append(f"{a:14}{n0:>10,.0f}{usd(t, 10)}{pct(t / n0 if t is not None and n0 else None, 9)}"
                 f"{usd(f5, 10)}{pct(chain([(x[2][a]['pnl'], x[2][a]['nav']) for x in s5]), 9)}"
                 f"{usd(fm, 10)}{pct(chain([(x[2][a]['pnl'], x[2][a]['nav']) for x in sm]), 9)}")
        tot["nav"] += n0
        tot["t"] += t or 0
        tot["f5"] += f5
        tot["fm"] += fm
    n = tot["nav"] or 1.0
    L.append(f"{'TOTAL':14}{tot['nav']:>10,.0f}{usd(tot['t'], 10)}{pct(tot['t'] / n, 9)}"
             f"{usd(tot['f5'], 10)}{pct(tot['f5'] / n, 9)}{usd(tot['fm'], 10)}{pct(tot['fm'] / n, 9)}")
    L.append(f"{'SPY':14}{'':>10}{'':>10}{pct(spy_day.get(bar), 9)}{'':>10}{pct(spy_chain([x[0] for x in s5]), 9)}"
             f"{'':>10}{pct(spy_chain([x[0] for x in sm]), 9)}")
    L.append(f"  5 sess = {s5[0][0] if s5 else 'n/a'} to {bar}. MTD = {len(sm)} sessions from "
             f"{sm[0][0] if sm else 'n/a'}. TOTAL % is on today's total value.")

    # 2. by side
    L += ["", "2. BY SIDE (all accounts)", f"  {'':24}{'today $':>9}{'5 sess $':>10}{'MTD $':>10}"]
    for k in ("longs", "shorts + inverse ETFs", "cash parking"):
        L.append(f"  {k:24}{usd(sum(today.get(a, {}).get(k, 0.0) for a in ACCOUNTS), 9)}"
                 f"{usd(sum(x[2][a][k] for x in s5 for a in ACCOUNTS), 10)}"
                 f"{usd(sum(x[2][a][k] for x in sm for a in ACCOUNTS), 10)}")
    names = sorted((pl, s_) for a in ACCOUNTS for pl, s_ in today.get(a, {}).get("names", []) if s_ not in CASH and pl)
    if names:
        L.append("  today's worst: " + ", ".join(f"{s_} {c:+.0f}" for c, s_ in names[:5]))
        L.append("  today's best:  " + ", ".join(f"{s_} {c:+.0f}" for c, s_ in names[::-1][:5]))
    mt = defaultdict(float)
    for x in sm:
        for a in ACCOUNTS:
            for pl, s_ in x[2][a]["names"]:
                if s_ not in CASH:
                    mt[s_] += pl
    if mt:
        rk = sorted(mt.items(), key=lambda kv: kv[1])
        L.append("  month's worst: " + ", ".join(f"{k} {v:+.0f}" for k, v in rk[:5]))
        L.append("  month's best:  " + ", ".join(f"{k} {v:+.0f}" for k, v in rk[::-1][:5]))

    # 3 + 4. exposure and what-if
    L += ["", "3. EXPOSURE (% of each account, from the morning snapshot, before that day's trades; no target, information only)",
          f"{'account':14}{'long':>8}{'short':>8}{'net':>8}{'gross':>8}{'cash':>8}{'beta-adj net':>14}"]
    what = []
    for a in ACCOUNTS:
        rows = book.get(a, [])
        n = sum(r["now"] for r in rows) or 1.0
        lng = sht = cash = badj = spx = rate = 0.0
        for r in rows:
            s, v = r["sym"], r["now"]
            if s in CASH:
                cash += v
                continue
            if r["qty"] < 0:
                sht += abs(v)
            elif s in INVERSE:
                sht += abs(v) * INVERSE[s]
            else:
                lng += v
            b = betas.get(s)
            if b:
                badj += b["spy"] * v
                spx += b["spy2"] * v * SPX_SHOCK
                rate += b["tlt2"] * v * TLT_PER_25BP
        L.append(f"{a:14}{lng / n * 100:>7.1f}%{sht / n * 100:>7.1f}%{(lng - sht) / n * 100:>+7.1f}%"
                 f"{(lng + sht) / n * 100:>7.1f}%{cash / n * 100:>7.1f}%{badj / n * 100:>+13.1f}%")
        top = sorted((r for r in rows if r["sym"] not in CASH), key=lambda r: -abs(r["now"]))[:5]
        L.append("    biggest: " + ", ".join(f"{r['sym']} {r['now'] / n * 100:+.1f}%" for r in top))
        what.append((a, n, spx, rate))
    L.append("  beta-adj net = each position x its 60-day beta to SPY (inverse ETFs and hedges count negative on their own).")

    L += ["", "4. WHAT IF (rough: 60-day betas to SPY and TLT, not a forecast)",
          f"{'account':14}{'SPX -3%':>12}{'':>9}{'10Y +25 bp':>12}"]
    for a, n, spx, rate in what:
        L.append(f"{a:14}{usd(spx, 12)}{pct(spx / n, 9)}{usd(rate, 12)}{pct(rate / n, 9)}")
    L.append(f"{'TOTAL':14}{usd(sum(w[2] for w in what), 12)}{'':>9}{usd(sum(w[3] for w in what), 12)}")

    # 5. conviction log
    L += ["", "5. CONVICTION LOG"]
    try:
        with open(CONV, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        L.append(f"  {len(rows)} calls logged since {min(r['date'] for r in rows)}")
        for src in ("desk", "kris"):
            sub = [r for r in rows if (r.get("source") or "").strip() == src]
            if not sub:
                continue
            tk = defaultdict(int)
            for r in sub:
                tk[(r.get("taken") or "").strip() or "blank"] += 1
            L.append(f"  {src}: {len(sub)} calls | " + ", ".join(f"{k} {v}" for k, v in sorted(tk.items(), key=lambda x: -x[1])))
        for g in ("A", "B", "C"):
            sub = [r for r in rows if (r.get("grade") or "").strip() == g]
            done = [r for r in sub if (r.get("outcome") or "").strip() not in ("", "superseded", "pending")]
            L.append(f"  grade {g}: {len(sub)} calls, {len(done)} with an outcome filled")
        nofill = sum(1 for r in rows if not (r.get("outcome") or "").strip())
        L.append(f"  {nofill} calls have no outcome yet, so no hit rate by grade can be shown.")
    except Exception as e:
        L.append(f"  could not read the log ({type(e).__name__})")

    L.append("")
    if missing:
        L.append("NO PRICE (left at the snapshot value, no P&L, no beta): " + ", ".join(sorted(missing)))
    nb = sorted(s for s in held if s not in betas and s not in missing)
    if nb:
        L.append("NO BETA (too little history): " + ", ".join(nb))
    L.append("Not in this version: closed-trade list (entry, exit, days, gain), intraday trading P&L, and the split by sleeve.")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    body = build()
    print(body)
    if not args.no_save:
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, f"{dt.date.today():%Y-%m-%d}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        print(f"\nsaved {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
