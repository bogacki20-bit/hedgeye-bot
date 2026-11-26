"""Score the conviction log: did each call go 3% our way before it went 3% against?

Method (the one the evening reviews have used by hand since 11/18, written down in
data/reviews/week_2026-11-20.md):
  - A call is RIGHT if price moved 3% the call's way before it moved 3% against, on daily bars
    AFTER the call day. WRONG is the mirror. Neither yet = open.
  - "Our way" comes from the action verb: SELL and SHORT want the price lower afterwards,
    BUY and COVER want it higher. (Selling an inverse fund is a SELL of that fund.)
  - Start price: the fill price when the call was taken; otherwise the 5-minute bar at the call
    time (yfinance); otherwise that day's close.
  - A day whose high and low cross both marks cannot be ordered on daily bars: "unclear".
  - Rows that are superseded, cancelled or never triggered are not scored.

Read-only by default. --commit writes outcome / outcome_date / days into the blank rows of
data/stalk/conviction_log.csv (a dated backup is saved first). Rows that already have an outcome
are never changed; --check re-scores them and prints where the tool and the hand score disagree.

    py -m tools.conviction_outcomes            # report only
    py -m tools.conviction_outcomes --check    # compare with the hand-scored rows
    py -m tools.conviction_outcomes --commit   # fill the blank rows
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import re
import shutil
import sys
from collections import defaultdict

from tools.execution_report import daily_bars, intraday_bars, price_at

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "stalk", "conviction_log.csv")
OUT_DIR = os.path.join(ROOT, "data", "reviews")
MARK = 0.03
SKIP_TAKEN = {"superseded", "cancelled", "not_triggered"}
SKIP_SIDE = {"cash_parking", "kinesis_swap"}
# log ticker -> yfinance symbol, where they differ
YF = {"BRK.B": "BRK-B", "BRKB": "BRK-B"}


def direction(action: str):
    """+1 when the call wants the price higher afterwards, -1 lower, None when unknown."""
    a = (action or "").strip().upper()
    if a.startswith(("BUY", "COVER")):
        return 1
    if a.startswith(("SELL", "SHORT")):
        return -1
    return None


def first_number(text: str):
    m = re.search(r"\d[\d,]*\.?\d*", (text or "").replace("$", ""))
    try:
        return float(m.group(0).replace(",", "")) if m else None
    except ValueError:
        return None


def score(bars, day, ref, way):
    """(outcome, date, sessions, move_our_way_to_last_close) from daily bars after `day`."""
    later = sorted(d for d in bars if d > day)
    if not later:
        return "open", None, 0, None
    for n, d in enumerate(later, 1):
        hi, lo, _ = bars[d]
        up, dn = hi >= ref * (1 + MARK), lo <= ref * (1 - MARK)
        good, bad = (up, dn) if way > 0 else (dn, up)
        if good and bad:
            return "unclear (both in one day)", d, n, None
        if good:
            return "right +3%", d, n, None
        if bad:
            return "wrong (3% against)", d, n, None
    last = bars[later[-1]][2]
    return "open", None, len(later), way * (last / ref - 1)


def build(commit: bool, check: bool) -> str:
    with open(LOG, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        fields = rd.fieldnames
        rows = list(rd)

    todo = []
    for r in rows:
        if (r.get("taken") or "").strip() in SKIP_TAKEN or (r.get("side") or "").strip() in SKIP_SIDE:
            continue
        if (r.get("outcome") or "").strip() in ("superseded", "cancelled"):
            continue
        way = direction(r.get("action"))
        if way is None:
            continue
        try:
            day = dt.date.fromisoformat(r["date"].strip())
        except ValueError:
            continue
        todo.append((r, way, day))

    syms = sorted({YF.get(r["ticker"].strip().upper(), r["ticker"].strip().upper()) for r, _, _ in todo})
    first = min(d for _, _, d in todo)
    daily = daily_bars(syms, first)
    intra = intraday_bars(syms)

    scored, no_price, disagree = [], [], []
    for r, way, day in todo:
        s = YF.get(r["ticker"].strip().upper(), r["ticker"].strip().upper())
        bars = daily.get(s)
        if not bars:
            no_price.append(r)
            continue
        fill_day = day
        ref = None
        if (r.get("taken") or "").strip() in ("yes", "partial"):
            ref = first_number(r.get("fill_price"))
            try:
                fill_day = dt.date.fromisoformat((r.get("fill_date") or "").strip()) or day
            except ValueError:
                fill_day = day
        src = "fill"
        # A fill in a different instrument (ADBE called, ADBG filled) is not on this ticker's scale.
        if ref and day in bars and abs(ref / bars[day][2] - 1) > 0.30:
            ref = None
        if not ref:
            ref, src = price_at(intra.get(s), day, r.get("time")), "call time"
        if not ref and day in bars:
            ref, src = bars[day][2], "close"
        if not ref:
            no_price.append(r)
            continue
        out, od, n, mtm = score(bars, max(day, fill_day), ref, way)
        had = (r.get("outcome") or "").strip()
        if had:
            if check and had[:5] != out[:5]:
                disagree.append((r, had, out, od, ref, src))
            out_used = had
        else:
            out_used = out
            if out != "open":
                r["outcome"], r["outcome_date"], r["days"] = out, od.isoformat(), str(n)
        scored.append((r, out_used, mtm))

    L = [f"CONVICTION LOG OUTCOMES  ({dt.datetime.now():%a %m/%d/%Y %H:%M} local)",
         f"Rule: 3% our way before 3% against, daily bars after the call day. {len(rows)} rows in the log.", ""]

    def line(label, sub):
        r_ = sum(1 for _, o, _ in sub if o.startswith("right"))
        w_ = sum(1 for _, o, _ in sub if o.startswith("wrong"))
        op = [m for _, o, m in sub if o == "open"]
        un = sum(1 for _, o, _ in sub if o.startswith("unclear"))
        done = r_ + w_
        rate = f"{r_}/{done} ({100 * r_ / done:.0f}%)" if done else "0/0"
        mt = [m for m in op if m is not None]
        avg = f", open avg {100 * sum(mt) / len(mt):+.1f}% our way" if mt else ""
        L.append(f"  {label:<26} right {rate:<14} open {len(op):<4}{' unclear ' + str(un) if un else ''}{avg}")

    def block(title, key):
        L.append(title)
        groups = defaultdict(list)
        for t in scored:
            groups[key(t[0])].append(t)
        for k in sorted(groups):
            line(k or "(none)", groups[k])
        L.append("")

    line("ALL SCORED CALLS", scored)
    L.append("")
    block("BY GRADE", lambda r: "grade " + ((r.get("grade") or "").strip() or "none"))
    block("BY KIND", lambda r: (r.get("side") or "").strip())
    block("WHO AND WHETHER TAKEN", lambda r: (
        "kris own trades" if (r.get("source") or "").strip() == "kris"
        else "desk, taken" if (r.get("taken") or "").strip() in ("yes", "partial")
        else "desk, not taken"))
    block("ENTRIES ONLY, BY GRADE", lambda r: (
        "grade " + ((r.get("grade") or "").strip() or "none")
        if (r.get("side") or "").strip() in ("long_entry", "long_add", "short_entry", "short_add") else "~ exits (not shown)"))

    skipped = len(rows) - len(todo)
    L.append(f"Not scored: {skipped} rows (superseded, cancelled, never triggered, cash parking, Kinesis swaps).")
    if no_price:
        L.append("No price found: " + ", ".join(sorted({r['ticker'] for r in no_price})))
    if check:
        L.append("")
        L.append(f"CHECK against the hand-scored rows: {len(disagree)} disagree.")
        for r, had, out, od, ref, src in disagree:
            L.append(f"  {r['date']} {r['time']} {r['ticker']:<6} {r['action'][:18]:<18} hand: {had:<20} tool: {out} "
                     f"(start {ref:.2f} from {src})")

    if commit:
        bak = LOG + f".bak_{dt.date.today().isoformat()}_outcomes"
        shutil.copy2(LOG, bak)
        with open(LOG, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        L.append("")
        L.append(f"Log updated (blank rows only). Backup: {os.path.basename(bak)}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="write outcomes into the blank rows of the log")
    ap.add_argument("--check", action="store_true", help="compare with rows scored by hand")
    a = ap.parse_args()
    text = build(a.commit, a.check)
    print(text)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"conviction_outcomes_{dt.date.today().isoformat()}.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
