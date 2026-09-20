"""book_export.py — generate the desk-LLM project-knowledge docs.

The trading-desk prompt (docs/TRADING_DESK_PROMPT.md) runs a short-inventory
clock and a BUXX pace check that both need data the prompt itself can't
carry: per-position ENTRY DATES and the accumulation ledger. This module
generates those two docs from the bot's own tables so they can be dropped
into the desk project's knowledge and refreshed daily:

  current-book.md   accounts, ticker, side, qty, cost basis, market value,
                    g/l %, entry date, days held, entry price, entry rp
  buxx-ledger.md    program terms, every buy, every distribution, pace

Entry date = start of the CURRENT holding streak, walked forward through
book_activity (seeded with the implied pre-history position so trades that
predate the activity table don't fake an entry). Entry rp = entry price vs
the Hedgeye range that was live ON the entry date (mfr_snapshots,
point-in-time — no lookahead). Missing data prints as '?' — never invented.

CLI:  python tools/book_export.py [--send]
      writes outputs/project_knowledge/*.md; --send ships both to Telegram.
"""

from __future__ import annotations

import datetime as dt
import pathlib

ACCOUNTS = {
    "X96383748": ("Individual", "long+short, $5,000 margin buffer"),
    "244859926": ("Rollover IRA", "long-only"),
    "245734604": ("Roth IRA", "long-only"),
}
CASH_LIKE = {"CLOX", "BUXX", "VTIP", "DBMF", "FDRXX", "SPAXX", "CORE"}
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "project_knowledge"


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def _positions():
    return _rows("""
        SELECT account_number, symbol, underlying, quantity, avg_cost,
               cost_basis, market_value, total_gl_pct, is_option,
               opt_expiry, opt_type, opt_strike, asset_class, snapshot_date
        FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
          AND asset_class <> 'cash'
        ORDER BY account_number, is_option, underlying""")


def _activity(acct, underlying, is_option, expiry, otype, strike):
    if is_option:
        return _rows("""
            SELECT run_date, side, quantity, price FROM book_activity
            WHERE account_number=%s AND underlying=%s AND is_option
              AND opt_expiry IS NOT DISTINCT FROM %s
              AND opt_type   IS NOT DISTINCT FROM %s
              AND opt_strike IS NOT DISTINCT FROM %s
              AND action_type IN ('option_open','option_close')
            ORDER BY run_date, id""", (acct, underlying, expiry, otype, strike))
    return _rows("""
        SELECT run_date, side, quantity, price FROM book_activity
        WHERE account_number=%s AND underlying=%s AND NOT is_option
          AND action_type IN ('buy','sell','reinvest')
        ORDER BY run_date, id""", (acct, underlying))


def _entry(acct, underlying, qty, is_option, expiry, otype, strike):
    """(entry_date|None, entry_price|None, pre_history: bool).

    Seed with the implied position BEFORE the activity window so a name
    held since before 2026-05-15 doesn't get a phantom entry date."""
    acts = _activity(acct, underlying, is_option, expiry, otype, strike)
    signed = [(d, (abs(float(q or 0)) if s == "buy" else -abs(float(q or 0))), p)
              for d, s, q, p in acts]
    pos = float(qty) - sum(x[1] for x in signed)   # implied start position
    entry, entry_px = None, None
    for d, dq, px in signed:
        prev = pos
        pos += dq
        if (abs(prev) < 1e-9 and abs(pos) > 1e-9) or prev * pos < -1e-9:
            entry, entry_px = d, (float(px) if px is not None else None)
    if entry is None:
        return None, None, bool(acts) or abs(pos) > 1e-9
    return entry, entry_px, False


def _entry_rp(ticker, entry_date, entry_px):
    if not entry_date or entry_px is None:
        return None
    r = _rows("""
        SELECT range_low, range_high FROM mfr_snapshots
        WHERE ticker=%s AND snapshot_date <= %s
          AND range_low IS NOT NULL AND range_high > range_low
        ORDER BY snapshot_date DESC LIMIT 1""", (ticker, entry_date))
    if not r:
        return None
    lo, hi = float(r[0][0]), float(r[0][1])
    return max(0.0, min(1.0, (entry_px - lo) / (hi - lo)))


def build_current_book() -> str:
    pos = _positions()
    if not pos:
        return "# CURRENT BOOK\n\n(no snapshot in book_positions)\n"
    snap = pos[0][13]
    today = dt.date.today()
    span = _rows("SELECT min(run_date) FROM book_activity")[0][0]

    long_mv = short_mv = 0.0
    by_acct: dict[str, list[str]] = {a: [] for a in ACCOUNTS}
    cash_rows: list[str] = []
    for (acct, sym, und, qty, avg, cb, mv, gl, is_opt,
         oexp, otyp, ostrk, _cls, _sd) in pos:
        qty, mv = float(qty or 0), float(mv or 0)
        side = "SHORT" if qty < 0 else "LONG"
        if und not in CASH_LIKE:
            long_mv += mv if mv > 0 else 0
            short_mv += mv if mv < 0 else 0
        ed, epx, pre = _entry(acct, und, qty, is_opt, oexp, otyp, ostrk)
        if ed:
            entered, held = str(ed), f"{(today - ed).days}d"
        elif pre and span:
            entered, held = f"pre-{span}", f">{(today - span).days}d"
        else:
            entered, held = "?", "?"
        # entry rp is meaningless for options (premium vs equity range)
        erp = None if is_opt else _entry_rp(
            und, ed, epx if epx is not None
            else (float(avg) if avg is not None else None))
        name = sym if not is_opt else f"{und} {oexp} {ostrk}{otyp}"
        tag = " [cash-like]" if und in CASH_LIKE else ""
        line = (f"| {name}{tag} | {side} | {qty:g} | "
                f"{f'${float(avg):,.2f}' if avg is not None else '?'} | "
                f"{f'${float(cb):,.0f}' if cb is not None else '?'} | ${mv:,.0f} | "
                f"{f'{float(gl):+.1f}%' if gl is not None else '?'} | "
                f"{entered} | {held} | "
                f"{f'${epx:,.2f}' if epx is not None else '?'} | "
                f"{f'{erp:.2f}' if erp is not None else '?'} |")
        (cash_rows if und in CASH_LIKE else by_acct.setdefault(acct, [])).append(
            line if und in CASH_LIKE else line)

    cash = _rows("""SELECT COALESCE(sum(market_value),0) FROM book_positions
                    WHERE snapshot_date=%s AND asset_class='cash'""", (snap,))[0][0]
    hdr = ("| position | side | qty | avg cost | cost basis | mkt val | g/l "
           "| entered | held | entry px | entry rp |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|")
    out = [f"# CURRENT BOOK — snapshot {snap} (generated {today})",
           "",
           "Auto-generated by the bot from Fidelity imports + trade history.",
           "Entry date = start of the current holding streak (the short clock",
           "runs off it). Entry rp = entry price vs the Hedgeye range live on",
           "that date (point-in-time). '?' = not in the data — never guessed.",
           f"**If this file is more than 3 sessions older than today, say so.**",
           "",
           "## Accounts"]
    for num, (nm, rules) in ACCOUNTS.items():
        out.append(f"- **{nm}** ({num}) — {rules}")
    for num, (nm, _r) in ACCOUNTS.items():
        rows = by_acct.get(num) or []
        if rows:
            out += ["", f"## {nm} ({num})", hdr, *rows]
    if cash_rows:
        out += ["", "## Cash-like sleeve (excluded from exposure math)", hdr, *cash_rows]
    net = long_mv + short_mv
    out += ["", "## Exposure (ex cash-like)",
            f"- Long ${long_mv:,.0f} · Short ${abs(short_mv):,.0f} · "
            f"Gross ${long_mv - short_mv:,.0f} · Net ${net:,.0f}",
            f"- Core cash ${float(cash):,.0f} (plus the cash-like sleeve above)",
            ""]
    return "\n".join(out)


def build_buxx_ledger() -> str:
    today = dt.date.today()
    start = dt.date(2026, 9, 1)
    buys = _rows("""
        SELECT run_date, quantity, price, amount FROM book_activity
        WHERE underlying='BUXX' AND action_type IN ('buy','reinvest')
        ORDER BY run_date""")
    divs = _rows("""
        SELECT run_date, amount FROM book_activity
        WHERE underlying='BUXX' AND action_type='income'
        ORDER BY run_date""")
    held = _rows("""
        SELECT COALESCE(sum(quantity),0), COALESCE(sum(market_value),0)
        FROM book_positions
        WHERE snapshot_date=(SELECT max(snapshot_date) FROM book_positions)
          AND underlying='BUXX'""")
    qty, mv = (float(held[0][0]), float(held[0][1])) if held else (0.0, 0.0)
    since = [b for b in buys if b[0] >= start]
    bought = sum(abs(float(b[3] or 0)) for b in since)
    months = max((today.year - start.year) * 12 + today.month - start.month + 1, 1)
    pace = months * 3000.0
    out = [f"# BUXX ACCUMULATION LEDGER — generated {today}",
           "",
           "Program: **$36,000/year (~$3,000/month)**, start 2026-09-01.",
           "Buy LOW in the band, preferably 0-7 days after the monthly",
           "distribution (~27th-30th). Savings-flow: EXEMPT from the trigger",
           "ladder, regime filter, and exposure math.",
           "",
           f"**Held:** {qty:g} sh · ${mv:,.0f}",
           f"**Bought since program start:** ${bought:,.0f} vs ${pace:,.0f} pace → "
           f"{'ON PACE' if bought >= pace else f'${pace - bought:,.0f} BEHIND'}",
           "",
           "## Buys (program window)",
           "| date | qty | price | amount |", "|---|---|---|---|"]
    out += [f"| {d} | {float(q or 0):g} | "
            f"{f'${float(p):,.2f}' if p is not None else '?'} | ${abs(float(a or 0)):,.0f} |"
            for d, q, p, a in since] or ["| (none yet) | | | |"]
    out += ["", "## Distributions received",
            "| date | amount |", "|---|---|"]
    out += [f"| {d} | ${abs(float(a or 0)):,.2f} |" for d, a in divs] or ["| (none) | |"]
    out += ["", "When Kris reports a buy that isn't here yet, treat his number",
            "as truth — this file lags the next Fidelity import.", ""]
    return "\n".join(out)


def write_files() -> list[pathlib.Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in (("current-book.md", build_current_book()),
                       ("buxx-ledger.md", build_buxx_ledger())):
        p = OUT_DIR / name
        p.write_text(text, encoding="utf-8")
        paths.append(p)
    return paths


def main() -> int:
    import sys
    paths = write_files()
    for p in paths:
        print("wrote", p)
    if "--send" in sys.argv:
        import os
        from telegram_handler import _send_message
        for p in paths:
            _send_message(os.environ["TELEGRAM_BOT_TOKEN"],
                          os.environ["TELEGRAM_CHAT_ID"],
                          {"document_name": p.name.replace(".md", ".txt"),
                           "document_text": p.read_text(encoding="utf-8"),
                           "caption": f"📓 {p.name} — drop into the desk project knowledge"})
            print("sent", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
