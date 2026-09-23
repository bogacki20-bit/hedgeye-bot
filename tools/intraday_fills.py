"""intraday_fills.py — Telegram FILL command (operator 9/22: BOOK RP told
him to sell things he'd already sold — the book is a 5 AM snapshot and
every intraday trade was invisible until the next morning's CSV).

v_book_effective ALREADY overlays post-snapshot rows from book_activity;
this command is the missing writer. Text a fill from the phone and every
surface that reads the effective book (BOOK RP, REPORT, screens' held
flag, shelf) updates immediately.

  FILL SOLD COP 3.719 @128.26        sell / trim a long
  FILL BOUGHT BNO 8.53 @58.61        buy / add
  FILL SHORTED WSM 1 @228.10         open/add short (sell)
  FILL COVERED ACI 10 @12.17         cover (buy)
  FILL SOLD OKTA 0.79 RIRA           account tag optional: IND|RIRA|ROTH
  FILL LIST                          today's texted fills
  FILL UNDO                          delete the last texted fill today

Price optional (amount omitted when absent). Rows are tagged
'TELEGRAM FILL' and are a BRIDGE, not a record: v_book_effective stops
overlaying them once the next morning snapshot lands, and the lot ledger
excludes them entirely (the CSV brings the authoritative rows).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re

log = logging.getLogger(__name__)

SENTINEL = "FILL"
TAG = "TELEGRAM FILL"
ACCOUNTS = {"IND": ("X96383748", "Individual"),
            "RIRA": ("244859926", "Rollover IRA"),
            "ROTH": ("245734604", "ROTH IRA")}

_VERBS = {"SOLD": ("sell", -1), "SELL": ("sell", -1),
          "BOUGHT": ("buy", 1), "BUY": ("buy", 1), "ADDED": ("buy", 1),
          "SHORTED": ("sell", -1), "SHORT": ("sell", -1),
          "COVERED": ("buy", 1), "COVER": ("buy", 1)}

_RE = re.compile(
    r"^FILL\s+(?P<verb>SOLD|SELL|BOUGHT|BUY|ADDED|SHORTED|SHORT|COVERED|COVER)\s+"
    r"(?P<tkr>[A-Z][A-Z0-9.]{0,6})\s+(?P<qty>[0-9.]+)"
    r"(?:\s*@\s*(?P<px>[0-9.]+))?"
    r"(?:\s+(?P<acct>IND|RIRA|ROTH))?\s*$", re.I)


def _exec(sql, args=()):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()
        return cur.rowcount


def _rows(sql, args=()):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def _effective_qty(tkr: str, acct: str):
    r = _rows("SELECT COALESCE(sum(quantity),0) FROM v_book_effective "
              "WHERE underlying=%s AND account_number=%s", (tkr, acct))
    return float(r[0][0]) if r else 0.0


def handle_fill_command(text: str):
    if not text:
        return None
    up = text.strip().upper()
    if not up.startswith(SENTINEL):
        return None

    if up in ("FILL", "FILL HELP"):
        return ("FILL <SOLD|BOUGHT|SHORTED|COVERED> <TKR> <qty> [@px] "
                "[IND|RIRA|ROTH]\nFILL LIST · FILL UNDO\n"
                "Bridges the book until tomorrow's CSV — every sheet "
                "updates immediately.")

    if up == "FILL LIST":
        rows = _rows("SELECT account_number, action_raw FROM book_activity "
                     "WHERE action_raw LIKE %s AND run_date=CURRENT_DATE "
                     "ORDER BY id", (TAG + "%",))
        if not rows:
            return "no texted fills today."
        return "today's texted fills:\n" + "\n".join(
            f"  {r[1][len(TAG) + 2:]}" for r in rows)

    if up == "FILL UNDO":
        n = _exec("DELETE FROM book_activity WHERE id = ("
                  "SELECT max(id) FROM book_activity WHERE action_raw LIKE %s "
                  "AND run_date=CURRENT_DATE)", (TAG + "%",))
        return "↩️ last texted fill deleted." if n else "nothing to undo today."

    m = _RE.match(up)
    if not m:
        return ("🛑 couldn't parse. Format: FILL SOLD COP 3.7 @128.26 "
                "[IND|RIRA|ROTH]")
    verb = m.group("verb")
    action_type, sign = _VERBS[verb]
    tkr = m.group("tkr")
    qty = float(m.group("qty"))
    px = float(m.group("px")) if m.group("px") else None
    acct_key = (m.group("acct") or "IND").upper()
    acct_no, acct_name = ACCOUNTS[acct_key]
    if acct_key != "IND" and verb in ("SHORTED", "SHORT"):
        return "🛑 IRAs are long-only — no shorts in RIRA/ROTH."

    signed_qty = sign * qty
    amount = (-sign) * qty * px if px is not None else None
    raw = (f"{TAG}: {verb} {tkr} {qty:g}"
           + (f" @{px:g}" if px else "") + f" ({acct_key})")
    h = hashlib.sha1(f"{raw}|{dt.datetime.now().isoformat()}".encode()).hexdigest()
    _exec("""
        INSERT INTO book_activity (run_date, account_number, account_name,
            action_raw, action_type, side, symbol, underlying, is_option,
            price, quantity, amount, row_hash)
        VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, FALSE,
                %s, %s, %s, %s)""",
          (acct_no, acct_name, raw, action_type, action_type, tkr, tkr,
           px, signed_qty, amount, h))
    eff = _effective_qty(tkr, acct_no)
    side = "flat" if abs(eff) < 1e-6 else ("long" if eff > 0 else "SHORT")
    return (f"✅ {raw}\n{tkr} effective position now: {eff:g} sh ({side}) — "
            f"all sheets updated. Tomorrow's CSV supersedes this bridge.")
