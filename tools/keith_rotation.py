"""Keith rotation log (Kris 11/22/2026: "track how Keith moves with rotating capital ... I would like to learn it").

One page per week: what Keith did in Portfolio Solutions each day, the size, what the name and the tape
did that day, where the name sat in its range, any list or trend change behind the move, what he said
about it (research digest), and what Kris traded in the same names that day.

Read-only. The page is saved under outputs/keith_rotation/ and holds Kris's fills: local, never commit.

    py -m tools.keith_rotation                  # the week ending last Friday
    py -m tools.keith_rotation --week 2026-11-20
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import re
import sys
import warnings

import db_pg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "outputs", "keith_rotation")
# Whose words count as Keith's own reason, best first. Anything else in the digest is context.
REASON_PRODUCTS = ("the_macro_show", "km_top3", "the_call_hedgeye", "the_call", "real_time_alert",
                   "portfolio_solutions", "10_etf_go_anywhere_portfolio", "early_look", "risk_range_signals")
_TICK = re.compile(r"\b[A-Z]{2,5}\b")
_SKIP = {"PA", "ETF", "ETFS", "BPS", "BP"}


def parse_commentary(text: str) -> list[dict]:
    """Keith's one-line note -> [{ticker, verb, size}]. size is 'min', 'all', a bp number, or None (new, no size)."""
    out = []
    body = re.sub(r"^.*?In the PA today,?\s*", "", text or "", flags=re.I | re.S)
    for sent in re.split(r"\.\s*", body):
        s = sent.strip()
        if not s:
            continue
        low = s.lower()
        verb = ("bought" if re.search(r"\b(bought|added|add|buy)\b", low) else
                "sold" if re.search(r"\b(sold|sell)\b", low) else None)
        if not verb:
            continue
        m = re.search(r"(\d+)\s*bps?", low)
        size = ("all" if re.search(r"\ball\b", low) else int(m.group(1)) if m else
                "min" if re.search(r"\bmins?\b", low) else None)
        for t in _TICK.findall(s):
            if t not in _SKIP:
                out.append(dict(ticker=t, verb=verb, size=size))
    return out


def _prices(tickers: list[str], start: dt.date, end: dt.date) -> dict:
    """ticker -> {date: (pct change on the day, close)} from Yahoo daily bars."""
    import yfinance as yf
    warnings.filterwarnings("ignore")
    out: dict = collections.defaultdict(dict)
    if not tickers:
        return out
    d = yf.download(tickers, start=str(start - dt.timedelta(days=7)), end=str(end + dt.timedelta(days=1)),
                    interval="1d", progress=False, group_by="ticker", threads=True, auto_adjust=False)
    for t in tickers:
        try:
            c = d[t]["Close"].dropna()
        except Exception:                                            # noqa: BLE001
            continue
        prev = None
        for i, v in c.items():
            if prev:
                out[t][i.date()] = ((float(v) / prev - 1) * 100, float(v))
            prev = float(v)
    return out


def build(week_end: dt.date) -> str:
    start = week_end - dt.timedelta(days=4)
    with db_pg.get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT action_date, commentary_raw FROM hedgeye_portfolio_actions "
                    "WHERE action_date BETWEEN %s AND %s ORDER BY 1", (start, week_end))
        notes = {d: c for d, c in cur.fetchall()}
        cur.execute("SELECT signal_date, ticker, action, bias FROM hedgeye_etf_pro_changes "
                    "WHERE signal_date BETWEEN %s AND %s", (start - dt.timedelta(days=3), week_end))
        etf = collections.defaultdict(list)
        for d, t, a, b in cur.fetchall():
            etf[t].append((d, a, b))
        cur.execute("SELECT ticker, signal_date, trend FROM hedgeye_risk_ranges "
                    "WHERE signal_date BETWEEN %s AND %s ORDER BY 1, 2", (start - dt.timedelta(days=7), week_end))
        flips, last = collections.defaultdict(list), {}
        for t, d, tr in cur.fetchall():
            if t in last and tr and last[t] and tr != last[t]:
                flips[t].append((d, last[t], tr))
            last[t] = tr
        acts = {d: parse_commentary(c) for d, c in notes.items()}
        names = sorted({a["ticker"] for v in acts.values() for a in v})
        cur.execute("SELECT ticker, snapshot_date, range_low, range_high FROM mfr_snapshots "
                    "WHERE ticker = ANY(%s) AND snapshot_date BETWEEN %s AND %s", (names, start, week_end))
        rng = {(t, d): (float(lo), float(hi)) for t, d, lo, hi in cur.fetchall() if lo and hi}
        cur.execute("SELECT signal_date, product, name, direction, thesis FROM research_calls "
                    "WHERE name = ANY(%s) AND signal_date BETWEEN %s AND %s", (names, start - dt.timedelta(days=1), week_end))
        why = collections.defaultdict(list)
        for d, p, n, di, th in cur.fetchall():
            why[n].append((d, p, di, th))
        cur.execute("SELECT run_date, account_name, side, symbol, price, quantity FROM book_activity "
                    "WHERE run_date BETWEEN %s AND %s AND side IN ('buy','sell') AND symbol IS NOT NULL AND symbol <> ''",
                    (start, week_end))
        mine = collections.defaultdict(list)
        for d, acct, side, sym, px, q in cur.fetchall():
            mine[d].append((acct, side, sym, float(px or 0), float(q or 0)))
        cur.execute("SELECT rank, ticker FROM hedgeye_portfolio_solutions WHERE snapshot_date = "
                    "(SELECT max(snapshot_date) FROM hedgeye_portfolio_solutions WHERE snapshot_date <= %s) ORDER BY 1",
                    (week_end,))
        ranked = [t for _, t in cur.fetchall()]

    px = _prices(names + ["SPY", "^VIX"], start, week_end)
    L = [f"# Keith's rotation, week of {start:%m/%d} to {week_end:%m/%d/%Y}", "",
         "Portfolio Solutions moves by day. Local file: it holds my fills, never commit it.", ""]
    tally = collections.Counter()
    seeds = []
    for d in sorted(acts):
        spy, vix = px["SPY"].get(d), px["^VIX"].get(d)
        L.append(f"## {d:%a %m/%d}  |  SPY {spy[0]:+.1f}%  |  VIX {vix[1]:.1f}" if spy and vix else f"## {d:%a %m/%d}")
        L.append(f"His words: {re.sub(chr(10), ' ', notes[d]).strip()}")
        L.append("")
        L.append("| Name | Move | Size | Name that day | rp at the close | Behind it | Pattern | What he said |")
        L.append("|---|---|---|---|---|---|---|---|")
        for a in acts[d]:
            t, verb, size = a["ticker"], a["verb"], a["size"]
            mv = px[t].get(d)
            day = f"{mv[0]:+.1f}%" if mv else "-"
            r = rng.get((t, d))
            rp = (mv[1] - r[0]) / (r[1] - r[0]) if (mv and r and r[1] > r[0]) else None
            behind = [f"ETF Pro {x} {b} {dd:%m/%d}" for dd, x, b in etf.get(t, []) if 0 <= (d - dd).days <= 3]
            behind += [f"trend {o} to {n} {dd:%m/%d}" for dd, o, n in flips.get(t, []) if 0 <= (d - dd).days <= 3]
            red = mv is not None and mv[0] <= -0.05
            flat = mv is not None and abs(mv[0]) < 0.05
            if size == "all":
                pat = "full exit" + (": lost its signal" if any("remove" in b or "Bearish" in b for b in behind) else "")
                tally["full exits"] += 1
            elif size in ("min", None) and verb == "bought":
                pat = "new position at minimum size"
                tally["new at minimum"] += 1
            elif verb == "bought":
                pat = "bought on a red day" if red else "bought on a flat day" if flat else "bought on a green day" if mv else "bought"
                tally["buys on red" if red else "buys on a flat day" if flat else "buys on green"] += 1
            else:
                pat = ("sold on a red day (cutting size)" if red else "trimmed on a flat day" if flat
                       else "trimmed on a green day" if mv else "sold")
                tally["sells on red" if red else "trims on a flat day" if flat else "trims on green"] += 1
            said = sorted((x for x in why.get(t, []) if x[0] <= d and x[1] in REASON_PRODUCTS),
                          key=lambda x: (x[0], -REASON_PRODUCTS.index(x[1])), reverse=True)
            quote = f"{said[0][3]} ({said[0][1]} {said[0][0]:%m/%d})" if said else "nothing stored"
            sz = "all" if size == "all" else "min" if size in ("min", None) else f"{size} bp"
            L.append(f"| {t} | {verb} | {sz} | {day} | {rp:.2f} |" if rp is not None else f"| {t} | {verb} | {sz} | {day} | - |")
            L[-1] += f" {'; '.join(behind) or '-'} | {pat} | {quote.replace('|', '/')} |"
            if size == "all" or (isinstance(size, int) and size >= 100) or "green day" in pat and verb == "bought" \
                    or "red day" in pat and verb == "sold":
                seeds.append(f"{d:%a %m/%d}: he {verb} {sz} {t} ({pat}; the name {day} that day)")
        bps_in = sum(a["size"] for a in acts[d] if a["verb"] == "bought" and isinstance(a["size"], int))
        bps_out = sum(a["size"] for a in acts[d] if a["verb"] == "sold" and isinstance(a["size"], int))
        L.append("")
        L.append(f"Sized moves: bought {bps_in} bp, sold {bps_out} bp (full exits and new positions not counted).")
        me = mine.get(d, [])
        same = [m for m in me if m[2] in {a['ticker'] for a in acts[d]}]
        L.append(f"Me that day: {sum(1 for m in me if m[1] == 'buy')} buys, {sum(1 for m in me if m[1] == 'sell')} sells."
                 + (" In his names: " + "; ".join(f"{m[0]} {m[1]} {abs(m[4]):g} {m[2]} at {m[3]:.2f}" for m in same) if same else
                    " None in the names he traded."))
        L.append("")
    L.append("## The week in counts")
    for k in ("new at minimum", "full exits", "buys on red", "buys on a flat day", "buys on green",
              "trims on green", "trims on a flat day", "sells on red"):
        L.append(f"- {k}: {tally.get(k, 0)}")
    touched = {a["ticker"] for v in acts.values() for a in v}
    if ranked:
        L.append(f"- His list by rank at the end of the week: {', '.join(ranked)}")
        L.append(f"- Held all week and never touched: {', '.join(t for t in ranked if t not in touched) or 'none'}")
    L.append("")
    L.append("## Quiz seeds (the weekend review picks one; I answer before seeing his words)")
    L += [f"- {s}" for s in seeds[:8]] or ["- none"]
    return "\n".join(L) + "\n"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", help="the Friday the week ends on, YYYY-MM-DD (default: last Friday)")
    args = ap.parse_args()
    if args.week:
        end = dt.date.fromisoformat(args.week)
    else:
        end = dt.date.today()
        while end.weekday() != 4:
            end -= dt.timedelta(days=1)
    page = build(end)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"week_{end}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    print(page)
    print(f"saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
