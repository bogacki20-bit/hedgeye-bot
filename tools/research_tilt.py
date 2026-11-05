"""Research tilt: turn the last few days of Hedgeye research into desk inputs (Kris, 11/5/2026).

Pulls Signal Strength, ETF Pro, Real-Time Alerts and digested reports together, then reads
them against the book:

    AGAINST   held names where research now points the other way, or a list dropped them
    WITH      held names research agrees with
    IDEAS     names not held where 2+ separate sources agree and none disagrees
    READS     the latest regime and process reads from the digests

It proposes. The rule card still decides, and only Kris changes a rule.

    py -m tools.research_tilt [--days 3]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

DIR = {"long": 1, "bullish": 1, "short": -1, "bearish": -1}
READ_PRODUCTS = ("early_look", "macro_show", "the_call_summary", "the_call", "market_situation_report")


def rta_signal(side: str | None, action: str | None, is_cover: bool) -> tuple[int, str]:
    """Pure: (direction, kind) of a Real-Time Alert. A cover or a booked gain closes a side."""
    if is_cover:
        return 0, "closing short"
    if side == "long" and action == "SELL":
        return 0, "closing long"
    return DIR.get(side or "", 0), "opening"


def read_held(side: int, signals: list[dict]) -> str:
    """Pure: 'against', 'with' or '' for a held name. Anything against wins."""
    if any(s["dir"] == -side or s.get("closes") == side for s in signals):
        return "against"
    return "with" if any(s["dir"] == side for s in signals) else ""


def idea_side(signals: list[dict]) -> tuple[int, int]:
    """Pure: (direction, separate sources agreeing) for a name not held; (0, 0) if they disagree."""
    dirs = {s["dir"] for s in signals if s["dir"]}
    if len(dirs) != 1:
        return 0, 0
    d = dirs.pop()
    return d, len({s["src"] for s in signals if s["dir"] == d})


def gather(cur, since: dt.date) -> dict:
    """{ticker: [signal]}: lists a name is on now, plus what changed since `since`."""
    out: dict[str, list[dict]] = {}

    def add(t, src, d, note, closes=0):
        out.setdefault(t, []).append({"src": src, "dir": d, "note": note, "closes": closes})

    cur.execute("SELECT ticker, side, added_on FROM ss_roster_current")
    for t, side, added in cur.fetchall():
        add(t, "SS", DIR.get(side or "", 0), f"on SS {side}" + (f", new {added:%m/%d}" if added and added >= since else ""))
    cur.execute("SELECT ticker, side, removed_on FROM ss_roster_history WHERE removed_on >= %s", (since,))
    for t, side, off in cur.fetchall():
        if not any(s["src"] == "SS" and s["dir"] == DIR.get(side or "", 0) for s in out.get(t, [])):
            add(t, "SS-off", 0, f"off SS {side} {off:%m/%d}", closes=DIR.get(side or "", 0))
    cur.execute("SELECT ticker, bias FROM hedgeye_etf_pro_ranges "
                "WHERE week_of = (SELECT max(week_of) FROM hedgeye_etf_pro_ranges)")
    for t, bias in cur.fetchall():
        add(t, "ETFPro", DIR.get(bias or "", 0), f"on ETF Pro {bias}")
    cur.execute("SELECT ticker, bias, signal_date, action FROM hedgeye_etf_pro_changes WHERE signal_date >= %s", (since,))
    latest_on = {t for t, v in out.items() if any(s["src"] == "ETFPro" for s in v)}
    for t, bias, day, action in cur.fetchall():
        if action == "remove" and t not in latest_on:
            add(t, "ETFPro-off", 0, f"off ETF Pro {bias} {day:%m/%d}", closes=DIR.get(bias or "", 0))
    cur.execute("SELECT ticker, side, action, is_cover_short, price, signal_date, signal_type FROM hedgeye_rta "
                "WHERE signal_date >= %s ORDER BY signal_date", (since,))
    for t, side, action, cover, px, day, kind in cur.fetchall():
        d, what = rta_signal(side, action, cover)
        closes = {"closing short": -1, "closing long": 1}.get(what, 0)
        add(t, "RTA", d, f"RTA {kind} {px or ''} {day:%m/%d}".replace("  ", " "), closes=closes)
    cur.execute("SELECT name, direction, product, signal_date FROM research_calls "
                "WHERE kind='ticker' AND basis='stated' AND signal_date >= %s", (since,))
    for t, direction, product, day in cur.fetchall():
        d = DIR.get(direction or "", 0)
        if d:
            add(t, "Research", d, f"{product} {direction} {day:%m/%d}")
    return out


def _clean(rows: list[dict]) -> str:
    seen = []
    for s in rows:
        if s["note"] not in seen:
            seen.append(s["note"])
    return "; ".join(seen)


def run(days: int) -> int:
    import db_pg
    db_pg._load_dotenv_fallback()
    today = dt.date.today()
    since = today - dt.timedelta(days=days)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT symbol, sum(quantity) FROM book_positions WHERE asset_class <> 'cash' AND "
                    "snapshot_date = (SELECT max(snapshot_date) FROM book_positions) GROUP BY 1")
        book = {t: (1 if float(q or 0) > 0 else -1) for t, q in cur.fetchall() if q}
        signals = gather(cur, since)
        cur.execute("SELECT ticker, hedgeye_bucket_0629 FROM ticker_tags WHERE hedgeye_bucket_0629 IS NOT NULL")
        monitor = dict(cur.fetchall())
        cur.execute("SELECT ticker, price, tv_trade, range_pos FROM v_screener")
        px = {t: (p, tr, rp) for t, p, tr, rp in cur.fetchall()}
        cur.execute("SELECT DISTINCT ON (product) product, signal_date, regime_read, process_read FROM research_digest "
                    "WHERE product = ANY(%s) AND signal_date >= %s AND (regime_read IS NOT NULL OR process_read IS NOT NULL) "
                    "ORDER BY product, signal_date DESC, id DESC", (list(READ_PRODUCTS), since))
        reads = cur.fetchall()

    def where(t: str) -> str:
        p, tr, rp = px.get(t, (None, None, None))
        if p is None:
            return "no price"
        vs = "" if tr is None else (" above Trade" if p > tr else " below Trade")
        return f"{float(p):,.2f}" + (f" rp {float(rp):.2f}" if rp is not None else "") + vs

    print(f"RESEARCH TILT {today:%m/%d}, last {days} days. Book = latest export.")
    print("\nAGAINST THE BOOK")
    n = 0
    for t, side in sorted(book.items()):
        rows = signals.get(t, [])
        if read_held(side, rows) == "against":
            n += 1
            pm = f" [Position Monitor: {monitor[t]}]" if t in monitor else ""
            print(f"  {t:6} {'long' if side > 0 else 'short':5} {where(t)} | {_clean(rows)}{pm}")
    print("  none" if not n else "", end="\n" if not n else "")
    with_book = sorted(t for t, s in book.items() if read_held(s, signals.get(t, [])) == "with")
    print(f"\nWITH THE BOOK ({len(with_book)})\n  " + " ".join(with_book))
    for label, want in (("IDEAS: SHORT", -1), ("IDEAS: LONG", 1)):
        ranked = []
        for t, rows in signals.items():
            d, count = idea_side(rows)
            if t not in book and d == want and count >= 2:
                ranked.append((count, t, rows))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        print(f"\n{label} (2+ sources; check the entry rule and earnings by hand)")
        for count, t, rows in ranked[:12]:
            print(f"  {count} {t:6} {where(t)} | {_clean(rows)}")
        if not ranked:
            print("  none")
    print("\nREADS")
    for product, day, regime, process in sorted(reads, key=lambda r: r[1], reverse=True):
        if regime:
            print(f"  {product} {day:%m/%d} regime: {regime[:220]}")
        if process:
            print(f"  {product} {day:%m/%d} process: {process[:220]}")
    return 0


def _cli() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    return run(ap.parse_args().days)


if __name__ == "__main__":
    sys.exit(_cli())
