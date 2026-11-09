"""Weekly scorecard: how the week's Fidelity fills measured up against the rule card.

    py -m tools.weekly_scorecard [--week-end YYYY-MM-DD]

Plain Python, no LLM calls. Needs that week's Fidelity files ingested first.

Lines are our own: Trade = TradingView Vola2vola as of the prior bar, Trend = 89-day EMA, 2%
buffer. Keith's numbers have no history to look up, and the desk's chat calls aren't stored, so
"exits" means exits the rule card required at the close.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import csv
import datetime as dt
import os
import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

from tools.rule_scanner import CASH, LONG_PM, SHORT_PM, TV_KEY, WATCH, is_etf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "outputs", "scorecards")
BUF = 0.02
HISTORY_WEEKS = 8
CHECKS = ("inventory", "lines", "rp", "day", "volume")


def _acct(name: str) -> str:
    return "Individual" if "Individual" in name else name


def load(week_start: dt.date, week_end: dt.date) -> dict:
    import db_pg
    db_pg._load_dotenv_fallback()
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select run_date, account_name, symbol, action_type, action_raw, quantity, price, amount
                         from book_activity
                        where action_type in ('buy','sell') and is_option is not true and symbol is not null
                          and quantity <> 0 and run_date <= %s
                        order by run_date, id""", (week_end,))
        fills = cur.fetchall()
        cur.execute("""select snapshot_date, account_name, symbol, quantity, market_value, cost_basis from book_positions
                        where snapshot_date between %s and %s and is_option is not true""",
                    (week_start - dt.timedelta(days=4), week_end + dt.timedelta(days=2)))
        rows = cur.fetchall()
        snaps = [r[:5] for r in rows]
        avg_cost = {(r[0], _acct(r[1]), r[2]): abs(float(r[5]) / float(r[3])) for r in rows if r[3] and r[5]}
        cur.execute("select ticker, side, added_on, removed_on from ss_roster_history")
        ss = cur.fetchall()
        cur.execute("select ticker, hedgeye_bucket_0629 from ticker_tags where hedgeye_bucket_0629 is not null")
        pm = dict(cur.fetchall())
        cur.execute("""select distinct on (ticker) ticker, bias from hedgeye_etf_pro_ranges
                        where week_of <= %s order by ticker, week_of desc""", (week_end,))
        etf = dict(cur.fetchall())
        cur.execute("""select ticker, bar_date, vola_b, range_high, range_low from tv_levels
                        where bar_date >= %s order by ticker, bar_date""",
                    (week_start - dt.timedelta(weeks=HISTORY_WEEKS + 2),))
        tv = collections.defaultdict(list)
        for t, d, v, rh, rl in cur.fetchall():
            tv[t].append((d, float(v) if v else None, float(rh) if rh else None, float(rl) if rl else None))
    watch = {}
    if os.path.exists(WATCH):
        with open(WATCH, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for t in r["ticker"].split():
                    watch[t] = r["side"]
    return dict(fills=fills, snaps=snaps, avg_cost=avg_cost, ss=ss, pm=pm, etf=etf, tv=tv, watch=watch)


def orders(fills) -> list[dict]:
    """Same-day fills of one ticker, account and intent become one order at the average price."""
    book = collections.OrderedDict()
    for d, acct, sym, _typ, raw, q, px, amt in fills:
        if sym in CASH or not re.fullmatch(r"[A-Z]{1,5}", sym):
            continue
        q = float(q)
        kind = ("short" if "SHORT SALE" in raw else "cover" if "SHORT COVER" in raw else "buy" if q > 0 else "sell")
        o = book.setdefault((d, _acct(acct), sym, kind), dict(date=d, acct=_acct(acct), sym=sym, kind=kind, qty=0.0, cash=0.0))
        o["qty"] += abs(q)
        o["cash"] += abs(float(amt or 0)) or abs(q) * float(px or 0)
    out = list(book.values())
    for o in out:
        o["px"] = o["cash"] / o["qty"]
    return out


def realize(all_orders) -> list[dict]:
    """FIFO P&L on closing orders. Shares bought before the data starts are left out."""
    lots = collections.defaultdict(collections.deque)
    closed = []
    for o in all_orders:
        k = (o["acct"], o["sym"], "short" if o["kind"] in ("short", "cover") else "long")
        if o["kind"] in ("buy", "short"):
            lots[k].append([o["qty"], o["px"]])
            continue
        left, pnl, matched = o["qty"], 0.0, 0.0
        while left > 1e-9 and lots[k]:
            lot = lots[k][0]
            n = min(left, lot[0])
            pnl += n * (o["px"] - lot[1]) * (1 if k[2] == "long" else -1)
            lot[0] -= n
            left -= n
            matched += n
            if lot[0] <= 1e-9:
                lots[k].popleft()
        if matched > 0:
            closed.append(dict(o, pnl=pnl, matched=matched))
    return closed


class Tape:
    """Daily bars and the lines as they stood at each date."""

    def __init__(self, tickers, tv):
        import yfinance as yf
        self.tv = tv
        self.d = yf.download(sorted(tickers) + ["SPY"], period="2y", interval="1d", progress=False,
                             threads=True, group_by="ticker", auto_adjust=False)

    def days(self, a: dt.date, b: dt.date) -> list[dt.date]:
        return [x.date() for x in self.d["SPY"].Close.dropna().index if a <= x.date() <= b]

    def at(self, t: str, day: dt.date) -> dict | None:
        try:
            h = self.d[t].dropna()
        except Exception:
            return None
        dates = [x.date() for x in h.index]
        i = bisect.bisect_right(dates, day) - 1
        if i < 25 or dates[i] != day:
            return None
        cl, vo = h.Close.values, h.Volume.values.astype(float)
        trend = float(h.Close.ewm(span=89, adjust=False).mean().iloc[i - 1])
        day_ret = cl[i] / cl[i - 1] - 1
        streak = 0
        for k in range(i, 0, -1):
            if cl[k] != cl[k - 1] and (cl[k] > cl[k - 1]) == (day_ret > 0):
                streak += 1
            else:
                break
        n = max(streak, 2)
        a20 = vo[i - 20:i].mean()
        res = dict(close=float(cl[i]), trend=trend, ret=day_ret, streak=streak,
                   vol_falling=bool(vo[i - n + 1:i + 1].mean() < a20 and vo[i] < vo[i - 1]), trade=None, rh=None, rl=None)
        rows = self.tv.get(TV_KEY.get(t, t), [])
        j = bisect.bisect_left([r[0] for r in rows], day) - 1
        if j >= 0 and (day - rows[j][0]).days <= 7:
            res.update(trade=rows[j][1], rh=rows[j][2], rl=rows[j][3])
        return res


def in_inventory(t, side, day, data) -> bool:
    live = any(x == t and s == side and a <= day and (r is None or r > day) for x, s, a, r in data["ss"])
    buckets = LONG_PM if side == "long" else SHORT_PM
    return live or data["pm"].get(t) in buckets or data["etf"].get(t) == side or data["watch"].get(t) == side


def entry_checks(o, b, data) -> dict:
    """True / False per check, None where the data isn't there."""
    side = "long" if o["kind"] == "buy" else "short"
    px, tr, tn = o["px"], b["trade"], b["trend"]
    rp = (px - b["rl"]) / (b["rh"] - b["rl"]) if b["rh"] and b["rl"] and b["rh"] > b["rl"] else None
    if side == "long":
        lines = None if tr is None else px >= tr * (1 - BUF) and px > tn * (1 - BUF)
        rp_ok = None if rp is None else 0 <= rp <= 0.35
        day_ok = b["ret"] < 0
    else:
        lines = None if tr is None else px <= tr * (1 + BUF) and px < tn * (1 + BUF)
        rp_ok = None if rp is None else rp >= 0.65
        day_ok = b["ret"] > 0 and b["streak"] >= 3
    return dict(inventory=in_inventory(o["sym"], side, o["date"], data), lines=lines, rp=rp_ok, day=day_ok, volume=b["vol_falling"])


def label_close(o, b) -> str:
    if b is None:
        return "no data"
    px, tr, tn = o["px"], b["trade"], b["trend"]
    rp = (px - b["rl"]) / (b["rh"] - b["rl"]) if b["rh"] and b["rl"] and b["rh"] > b["rl"] else None
    if o["kind"] == "sell":
        if px < tn * (1 - BUF):
            return "long: below Trend"
        if tr and px < tr * (1 - BUF):
            return "long: below Trade"
        return "long: trim above range" if rp is not None and rp > 1 else "long: other"
    if px > tn * (1 + BUF):
        return "short: above Trend"
    if tr and px > tr * (1 + BUF):
        return "short: above Trade"
    return "short: range low" if rp is not None and rp <= 0.25 else "short: other"


def snapshot_by_day(snaps) -> dict:
    out = collections.defaultdict(list)
    for d, acct, sym, q, mv in snaps:
        out[d].append((_acct(acct), sym, float(q or 0), float(mv or 0)))
    return out


def exit_rule(q, b, avg) -> str | None:
    """The first exit the rule card calls for at this close, if any."""
    rp = (b["close"] - b["rl"]) / (b["rh"] - b["rl"]) if b["rh"] and b["rl"] and b["rh"] > b["rl"] else None
    if q > 0:
        if b["close"] < b["trend"] * (1 - BUF):
            return "below Trend"
        return "trim, rp > 1" if rp is not None and rp > 1 else None
    if b["trade"] and b["close"] > b["trade"] * (1 + BUF):
        return "above Trade"
    gain = avg / b["close"] - 1 if avg else 0
    return "cover target" if gain >= 0.03 or (rp is not None and rp <= 0.25) else None


def exit_audit(by_snap, tape, week, all_orders, avg_cost) -> list[dict]:
    """Each position the rule card said to exit at a close, and whether it was acted on within 2 sessions."""
    snap_days = sorted(by_snap)
    last_fill = max(o["date"] for o in all_orders)
    next_days = tape.days(week[0], week[-1] + dt.timedelta(days=7))
    done, out = set(), []
    for day in week:
        i = bisect.bisect_right(snap_days, day) - 1
        if i < 0:
            continue
        for acct, sym, q, _mv in by_snap[snap_days[i]]:
            if sym in CASH or not q or (acct, sym) in done or not re.fullmatch(r"[A-Z]{1,5}", sym):
                continue
            b = tape.at(sym, day)
            if b is None:
                continue
            why = exit_rule(q, b, avg_cost.get((snap_days[i], acct, sym)))
            if why is None:
                continue
            done.add((acct, sym))
            k = next_days.index(day)
            window = next_days[k: k + 3]
            kinds = ("sell",) if q > 0 else ("cover",)
            acted = any(o["acct"] == acct and o["sym"] == sym and o["kind"] in kinds and window[0] <= o["date"] <= window[-1]
                        for o in all_orders)
            status = "done" if acted else ("pending" if len(window) < 3 or window[-1] > last_fill else "missed")
            out.append(dict(day=day, acct=acct, sym=sym, why=why, status=status))
    return out


def under_min(by_snap, week) -> tuple[collections.Counter, dict]:
    """Position-days under the class MIN: short 0.5%, ETF 2%, stock 1%."""
    days = [d for d in sorted(by_snap) if week[0] <= d <= week[-1] + dt.timedelta(days=1)]
    syms = {s for d in days for _a, s, _q, _m in by_snap[d] if s not in CASH and re.fullmatch(r"[A-Z]{1,5}", s or "")}
    with ThreadPoolExecutor(8) as ex:
        etf = dict(zip(sorted(syms), ex.map(is_etf, sorted(syms))))
    count: collections.Counter = collections.Counter()
    last: dict = {}
    for d in days:
        total = collections.Counter()
        for a, _s, _q, m in by_snap[d]:
            total[a] += m
        last = {}
        for a, s, q, m in by_snap[d]:
            if s not in syms or not q or not total[a]:
                continue
            pct = abs(m) / total[a] * 100
            floor = 0.5 if q < 0 else 2 if etf.get(s) else 1
            if pct < floor:
                count[(a, s)] += 1
                last[(a, s)] = (pct, floor)
    return count, last


def cash_table(by_snap, week) -> dict:
    days = [d for d in sorted(by_snap) if week[0] <= d <= week[-1] + dt.timedelta(days=1)]
    rows = collections.defaultdict(list)
    for d in days:
        tot, cash, longs = collections.Counter(), collections.Counter(), collections.Counter()
        for a, s, q, m in by_snap[d]:
            tot[a] += m
            if s in CASH or (s or "").endswith("**"):
                cash[a] += m
            elif q > 0:
                longs[a] += 1
        for a in tot:
            rows[a].append((cash[a] / tot[a] * 100 if tot[a] else 0, longs[a], tot[a]))
    return rows


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _rule_rows(items) -> list[str]:
    by = collections.defaultdict(list)
    for c in items:
        by[c["rule"]].append(c["pnl"])
    rows = []
    for rule, pnl in sorted(by.items()):
        wins = [p for p in pnl if p > 0]
        loss = [p for p in pnl if p <= 0]
        rows.append(f"| {rule} | {len(pnl)} | {len(wins) / len(pnl) * 100:.0f}% | "
                    f"{_money(sum(wins) / len(wins)) if wins else '-'} | {_money(sum(loss) / len(loss)) if loss else '-'} | {_money(sum(pnl))} |")
    return rows


def build(week_end: dt.date) -> str:
    week_start = week_end - dt.timedelta(days=4)
    data = load(week_start, week_end)
    every = orders(data["fills"])
    history_start = week_start - dt.timedelta(weeks=HISTORY_WEEKS)
    in_week = [o for o in every if week_start <= o["date"] <= week_end]
    closed = realize(every)
    recent = [c for c in closed if c["date"] >= history_start]
    by_snap = snapshot_by_day(data["snaps"])
    held = {s for rows in by_snap.values() for _a, s, _q, _m in rows if s not in CASH and re.fullmatch(r"[A-Z]{1,5}", s or "")}
    tape = Tape({o["sym"] for o in in_week} | {c["sym"] for c in recent} | held, data["tv"])
    week = tape.days(week_start, week_end)

    entries = []
    for o in in_week:
        if o["kind"] in ("buy", "short"):
            b = tape.at(o["sym"], o["date"])
            entries.append((o, entry_checks(o, b, data) if b else None))
    audit = exit_audit(by_snap, tape, week, every, data["avg_cost"])
    for c in recent:
        c["rule"] = label_close(c, tape.at(c["sym"], c["date"]))
    wk_closed = [c for c in recent if c["date"] >= week_start]
    count, last = under_min(by_snap, week)
    cash = cash_table(by_snap, week)

    judged = [a for a in audit if a["status"] != "pending"]
    done = sum(a["status"] == "done" for a in judged)
    scored = [(o, c) for o, c in entries if c]
    passed = [(o, c) for o, c in scored if all(c.values())]
    known = [(o, c) for o, c in scored if all(v is not False for v in c.values()) and None in c.values()]
    fails = collections.Counter(k for _o, c in scored for k in CHECKS if c[k] is False)

    L = [f"# Weekly scorecard: week ending {week_end}", ""]
    L += [f"- **Rule exits acted on within 2 sessions:** {done} of {len(judged)}" + (f" ({len(audit) - len(judged)} still open)" if len(audit) > len(judged) else ""),
          f"- **Entries that passed all 5 checks:** {len(passed)} of {len(scored)}" + (f" ({len(known)} more had a check with no data)" if known else ""),
          f"- **Closed this week:** {_money(sum(c['pnl'] for c in wk_closed))} across {len(wk_closed)} closing orders",
          f"- **Position-days under MIN:** {sum(count.values())}", ""]
    L += ["## Exits the rules required", "| Close | Account | Ticker | Rule | Status |", "|---|---|---|---|---|"]
    L += [f"| {a['day']} | {a['acct']} | {a['sym']} | {a['why']} | {a['status']} |" for a in audit] or ["| none | | | | |"]
    L += ["", "## Entries", "| Date | Account | Side | Ticker | $ | Passed | Failed |", "|---|---|---|---|---|---|---|"]
    for o, c in sorted(entries, key=lambda x: (x[0]["date"], x[0]["acct"], x[0]["sym"])):
        side = "long" if o["kind"] == "buy" else "short"
        if c is None:
            L.append(f"| {o['date']} | {o['acct']} | {side} | {o['sym']} | {o['cash']:,.0f} | no data | |")
            continue
        bad = [k for k in CHECKS if c[k] is False] + [f"{k}?" for k in CHECKS if c[k] is None]
        L.append(f"| {o['date']} | {o['acct']} | {side} | {o['sym']} | {o['cash']:,.0f} | {sum(v is True for v in c.values())}/5 | {', '.join(bad)} |")
    L += ["", "Most-failed checks: " + (", ".join(f"{k} {n}" for k, n in fails.most_common()) or "none"), ""]
    head = ["| Rule | Closes | Win rate | Avg win | Avg loss | Total |", "|---|---|---|---|---|---|"]
    L += ["## Win vs loss by exit rule: this week"] + head + (_rule_rows(wk_closed) or ["| none | | | | | |"])
    L += ["", f"## Win vs loss by exit rule: last {HISTORY_WEEKS} weeks"] + head + (_rule_rows(recent) or ["| none | | | | | |"])
    L += ["", "## Under MIN at the last snapshot", "| Account | Ticker | % of account | MIN | Snapshots under MIN |", "|---|---|---|---|---|"]
    L += [f"| {a} | {s} | {p:.2f}% | {f:g}% | {count[(a, s)]} |" for (a, s), (p, f) in sorted(last.items())] or ["| none | | | | |"]
    L += ["", "## Cash and position count (cash includes BUXX)", "| Account | Avg cash | Last cash | Longs | Value |", "|---|---|---|---|---|"]
    for a, v in sorted(cash.items()):
        L.append(f"| {a} | {sum(x[0] for x in v) / len(v):.0f}% | {v[-1][0]:.0f}% | {v[-1][1]} | {_money(v[-1][2])} |")
    L += ["", "Lines are our own (Vola2vola Trade, 89-day EMA Trend, 2% buffer), so a name Keith has at a different level can score differently. "
          "Lower-high cuts to MIN aren't scored: past range structure isn't stored."]
    return "\n".join(L) + "\n"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--week-end", type=dt.date.fromisoformat)
    a = ap.parse_args()
    today = dt.date.today()
    end = a.week_end or today - dt.timedelta(days=(today.weekday() - 4) % 7)
    text = build(end)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"scorecard_{end}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("saved", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
