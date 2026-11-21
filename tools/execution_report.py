"""execution_report.py - how well the trades were executed (Kris 11/21/2026).

Read-only. Prints the report and saves it to outputs/execution_report/<date>.txt (local only: it holds the book).

  1. Dollars by who made the entry: desk calls taken, own trades behind a Keith alert, other own trades
  2. How far each closed trade went against and for before the exit, and what a tighter stop would have done
  3. Stops, breaks and removals: how late the fill was and what the delay cost
  4. Every other desk call taken: price when the call went out vs the fill
  5. Days held: winners vs losers
  6. Desk calls passed on: what they did

Where the numbers come from, and their limits:
  - Trades: Fidelity activity (book_activity), matched first in, first out, the same way as eod_report.
    Fidelity gives a date for each fill but no time.
  - Who made the entry: data/stalk/conviction_log.csv, which starts 11/16/2026. Entries before that, or with
    no log row, show as "not in the log".
  - Against / for: daily highs and lows (yfinance) from the day after the entry to the exit day. The entry
    day itself is left out because the fill time is unknown, so same-day round trips are not measured.
  - Price at the call: the 5-minute bar (yfinance, about 60 days back) that holds the call's time. A call
    made before the open uses the 9:30 open.

  py -m tools.execution_report              # closes in the last 30 days
  py -m tools.execution_report --days 90
  py -m tools.execution_report --no-save
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import statistics
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "outputs", "execution_report")
CONV = os.path.join(ROOT, "data", "stalk", "conviction_log.csv")

ACCT = {"individual": "Individual", "rollover": "Rollover IRA", "roth": "ROTH IRA"}
KEITH = re.compile(r"keith|\brta\b|etf pro|alpha code|\bpa bought", re.I)
STOP = re.compile(r"stop|break|removed|removal|-5%", re.I)
STOPS_TESTED = (2, 3, 4, 5)


def read_log():
    if not os.path.exists(CONV):
        return []
    rows = list(csv.DictReader(open(CONV, encoding="utf-8")))
    for r in rows:
        r["acct"] = ACCT.get(r["account"].strip().lower().split("+")[0], r["account"])
        r["sym"] = r["ticker"].strip().upper()
        r["verb"] = (r["action"].split() or [""])[0].upper()
        try:
            r["d"] = dt.date.fromisoformat(r["date"])
        except ValueError:
            r["d"] = None
        try:
            r["fd"] = dt.date.fromisoformat(r["fill_date"]) if r["fill_date"] else None
        except ValueError:
            r["fd"] = None
        try:
            r["fpx"] = float(r["fill_price"])
        except ValueError:
            r["fpx"] = None
    return [r for r in rows if r["d"]]


def entry_type(log, acct, sym, opened):
    """Who made the entry: the newest BUY row for that account, name and day."""
    hits = [r for r in log if r["sym"] == sym and r["acct"] == acct and r["verb"] == "BUY"
            and (r["fd"] or r["d"]) == opened and r["taken"] in ("yes", "partial")]
    if not hits:
        return "not in the log"
    r = hits[-1]
    if r["source"] == "desk":
        return "desk call"
    return "own, behind Keith" if KEITH.search(r["note"]) else "own, other"


def daily_bars(symbols, start):
    import yfinance as yf
    syms = sorted(set(symbols))
    if not syms:
        return {}
    d = yf.download(syms, start=start - dt.timedelta(days=5), interval="1d", progress=False, threads=True,
                    group_by="ticker", auto_adjust=False)
    out = {}
    for s in syms:
        try:
            f = d[s].dropna(subset=["Close"])
        except Exception:
            continue
        out[s] = {i.date(): (float(r["High"]), float(r["Low"]), float(r["Close"])) for i, r in f.iterrows()}
    return out


def intraday_bars(symbols):
    import yfinance as yf
    syms = sorted(set(symbols))
    if not syms:
        return {}
    d = yf.download(syms, period="1mo", interval="5m", progress=False, threads=True, group_by="ticker",
                    auto_adjust=False, prepost=False)
    out = {}
    for s in syms:
        try:
            f = d[s].dropna(subset=["Close"])
        except Exception:
            continue
        idx = f.index.tz_convert("America/New_York") if f.index.tz is not None else f.index
        out[s] = {(i.date(), i.strftime("%H:%M")): (float(o), float(c)) for i, o, c in zip(idx, f["Open"], f["Close"])}
    return out


def price_at(bars, d, hhmm):
    """Close of the 5-minute bar holding hh:mm; the 9:30 open for a call before the open. None if unknown."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (hhmm or "").strip())
    if not m or not bars:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if (h, mi) < (9, 30):
        b = bars.get((d, "09:30"))
        return b[0] if b else None
    if (h, mi) >= (16, 0):
        b = bars.get((d, "15:55"))
        return b[1] if b else None
    b = bars.get((d, f"{h:02d}:{mi - mi % 5:02d}"))
    return b[1] if b else None


def dollars(qty_text, px):
    t = (qty_text or "").replace(",", "")
    m = re.search(r"\$\s*([\d.]+)", t)
    if m:
        return float(m.group(1))
    m = re.search(r"([\d.]+)\s*sh", t) or re.fullmatch(r"\s*([\d.]+)\s*", t)
    return float(m.group(1)) * px if m and px else None


def med(xs):
    return statistics.median(xs) if xs else float("nan")


def stats_line(label, ps):
    if not ps:
        return f"  {label:24} none"
    w = [p for p in ps if p["gain"] > 0]
    lo = [p for p in ps if p["gain"] < 0]
    return (f"  {label:24}{len(ps):>4} | won {len(w) / len(ps) * 100:>3.0f}% | total {sum(p['gain'] for p in ps):>+8,.0f}"
            f" | avg win {sum(p['gain'] for p in w) / len(w) if w else 0:>+7.1f}"
            f" | avg loss {sum(p['gain'] for p in lo) / len(lo) if lo else 0:>+7.1f}")


def build(days: int) -> str:
    from tools import eod_report as E
    pieces, lots, skipped, first, latest = E.fifo()
    log = read_log()
    bar = latest or dt.date.today()
    since = bar - dt.timedelta(days=days)

    # one trade = one lot's pieces closed on one day
    agg = defaultdict(lambda: dict(qty=0.0, cost=0.0, proceeds=0.0, gain=0.0))
    for p in pieces:
        if p["closed"] < since:
            continue
        g = agg[(p["acct"], p["sym"], p["side"], p["opened"], p["closed"])]
        g["qty"] += p["qty"]
        g["cost"] += p["qty"] * p["entry"]
        g["proceeds"] += p["qty"] * p["exit"]
        g["gain"] += p["gain"]
    trades = [dict(acct=k[0], sym=k[1], side=k[2], opened=k[3], closed=k[4], qty=g["qty"], cost=g["cost"],
                   entry=g["cost"] / g["qty"], exit=g["proceeds"] / g["qty"], gain=g["gain"],
                   who=entry_type(log, k[0], k[1], k[3])) for k, g in agg.items() if g["qty"]]

    open_lots = [dict(acct=a, sym=s, opened=l[0], qty=l[1], entry=l[2], who=entry_type(log, a, s, l[0]))
                 for (a, s), ls in lots.items() for l in ls if abs(l[1]) > 1e-9 and l[0] >= since]

    taken = [r for r in log if r["source"] == "desk" and r["taken"] in ("yes", "partial") and r["fpx"]]
    daily = daily_bars([t["sym"] for t in trades] + [l["sym"] for l in open_lots], since - dt.timedelta(days=40))
    intra = intraday_bars([r["sym"] for r in taken])

    L = [f"EXECUTION REPORT  closes {since:%m/%d} to {bar:%m/%d/%Y}  (Fidelity activity through {latest})", ""]

    # 1 ------------------------------------------------------------------------------------------
    L.append("1. DOLLARS BY WHO MADE THE ENTRY (closed trades; the log starts 11/16)")
    order = ["desk call", "own, behind Keith", "own, other", "not in the log"]
    for w in order:
        L.append(stats_line(w, [t for t in trades if t["who"] == w]))
    L.append(stats_line("ALL", trades))
    L.append("   Still open, marked at the last close:")
    for w in order:
        ls = [l for l in open_lots if l["who"] == w]
        tot, n = 0.0, 0
        for l in ls:
            px = daily.get(l["sym"], {})
            if not px:
                continue
            last = px[max(px)][2]
            tot += l["qty"] * (last - l["entry"])
            n += 1
        L.append(f"  {w:24}{n:>4} lots | open gain {tot:>+8,.0f}")

    # 2 ------------------------------------------------------------------------------------------
    L += ["", "2. HOW FAR AGAINST AND FOR, BEFORE THE EXIT (daily highs and lows after the entry day)"]
    meas = []
    for t in trades:
        px = daily.get(t["sym"], {})
        ds = [d for d in px if t["opened"] < d <= t["closed"]]
        if not ds:
            continue
        hi, lo = max(px[d][0] for d in ds), min(px[d][1] for d in ds)
        sign = 1 if t["side"] == "long" else -1
        t["against"] = min(0.0, ((lo if sign > 0 else hi) / t["entry"] - 1) * sign * 100)
        t["for_"] = max(0.0, ((hi if sign > 0 else lo) / t["entry"] - 1) * sign * 100)
        t["ret"] = (t["exit"] / t["entry"] - 1) * sign * 100
        meas.append(t)
    win = [t for t in meas if t["gain"] > 0]
    los = [t for t in meas if t["gain"] < 0]
    L.append(f"  measured {len(meas)} of {len(trades)} trades (same-day round trips and names with no price are left out)")
    if win:
        L.append(f"  winners {len(win):>4}: typical worst point {med([t['against'] for t in win]):+.1f}% | "
                 f"typical best point {med([t['for_'] for t in win]):+.1f}% | typical exit {med([t['ret'] for t in win]):+.1f}%")
    if los:
        up1 = sum(1 for t in los if t["for_"] >= 1.0)
        L.append(f"  losers  {len(los):>4}: typical worst point {med([t['against'] for t in los]):+.1f}% | "
                 f"typical best point {med([t['for_'] for t in los]):+.1f}% | typical exit {med([t['ret'] for t in los]):+.1f}%")
        L.append(f"  {up1} of {len(los)} losers were up 1% or more at some point before they lost")
    if meas:
        actual = sum(t["gain"] for t in meas)
        L.append(f"  What a fixed stop would have done to these {len(meas)} trades (actual total {actual:+,.0f}):")
        for x in STOPS_TESTED:
            tot, cut_w, cut_l = 0.0, 0, 0
            for t in meas:
                if t["against"] <= -x:
                    tot += -x / 100 * t["cost"]
                    cut_w += t["gain"] > 0
                    cut_l += t["gain"] <= 0
                else:
                    tot += t["gain"]
            L.append(f"    stop at -{x}%: total {tot:>+8,.0f} ({tot - actual:>+7,.0f} vs actual) | "
                     f"stopped out {cut_w} winners and {cut_l} losers early")
        L.append("    (Daily bars only: a stop is assumed filled exactly at its level, and gaps would fill worse.)")

    # 3 and 4 ------------------------------------------------------------------------------------
    rows = []
    for r in taken:
        call = price_at(intra.get(r["sym"]), r["d"], r["time"])
        if not call:
            continue
        sign = 1 if r["verb"] == "SELL" else -1
        gap = (r["fpx"] / call - 1) * sign * 100
        usd = dollars(r["fill_qty"], r["fpx"])
        rows.append(dict(r=r, call=call, gap=gap, usd=usd, late=((r["fd"] or r["d"]) - r["d"]).days,
                         stop=bool(STOP.search(r["action"] + " " + r["note"]))))

    def gap_block(title, rs):
        out = ["", title]
        if not rs:
            return out + ["  none with a usable call time and fill price"]
        cost = sum(x["gap"] / 100 * x["usd"] for x in rs if x["usd"])
        out.append(f"  {len(rs)} calls | typical gap {med([x['gap'] for x in rs]):+.2f}% | average {sum(x['gap'] for x in rs) / len(rs):+.2f}%"
                   f" | worse than the call price on {sum(1 for x in rs if x['gap'] < 0)} | in dollars {cost:+,.0f}"
                   f" | filled a day or more late: {sum(1 for x in rs if x['late'] > 0)}")
        out.append("  (+ = the fill was better than the price when the call went out)")
        worst = sorted(rs, key=lambda x: x["gap"])[:8]
        out.append(f"    {'call':11} {'name':6} {'action':22} {'call px':>9} {'fill':>9} {'gap':>7} {'days late':>9}")
        for x in worst:
            r = x["r"]
            out.append(f"    {r['d']:%m/%d} {r['time']:5} {r['sym']:6} {r['action'][:22]:22} {x['call']:>9.2f} {r['fpx']:>9.2f}"
                       f" {x['gap']:>+6.2f}% {x['late']:>9}")
        return out

    L += gap_block("3. STOPS, BREAKS AND REMOVALS: THE DELAY AND WHAT IT COST (worst 8 shown)", [x for x in rows if x["stop"]])
    L += gap_block("4. EVERY OTHER DESK CALL TAKEN: PRICE AT THE CALL VS THE FILL (worst 8 shown)", [x for x in rows if not x["stop"]])

    # 5 ------------------------------------------------------------------------------------------
    L += ["", "5. DAYS HELD"]
    for label, ps in (("winners", [t for t in trades if t["gain"] > 0]), ("losers", [t for t in trades if t["gain"] < 0])):
        if ps:
            hd = [(t["closed"] - t["opened"]).days for t in ps]
            L.append(f"  {label:8}{len(ps):>4}: typical {med(hd):.0f} days | average {sum(hd) / len(hd):.1f} | longest {max(hd)}")

    # 6 ------------------------------------------------------------------------------------------
    L += ["", "6. DESK CALLS PASSED ON (taken = no), BY WHAT THEY DID NEXT"]
    passed = [r for r in log if r["source"] == "desk" and r["taken"] == "no" and r["outcome"] not in ("", "superseded")]
    by = defaultdict(lambda: [0, 0, 0])
    for r in passed:
        o = r["outcome"].lower()
        by[r["grade"] or "-"][0 if o.startswith("right") else 1 if o.startswith("wrong") else 2] += 1
    if not by:
        L.append("  none with an outcome yet")
    for g in sorted(by):
        a, b, c = by[g]
        L.append(f"  grade {g}: {a} went the call's way, {b} went against it, {c} other")

    L += ["", f"Notes: {skipped} sells of positions opened before {first} are left out. Dividends and fees are not in the gains. "
              "Small samples: read the direction, not the decimals."]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    text = build(a.days)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(text)
    if not a.no_save:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, f"{dt.date.today():%Y-%m-%d}.txt"), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
