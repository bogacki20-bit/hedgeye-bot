"""Source-agnostic nightly MFR 'to-add' batch.

READS ONLY — roster tables via db_pg + mfr_client.list_watchlist(); no MFR write,
no write token. Unions "names added today" across ALL registered EnrollableSources,
removes anything already active in MFR (and known-uncoverable), and Telegrams a clean
space-separated to-add list to paste into MFR → Activate Assets. Quiet on empty nights;
throttled once/night.

Adding a future source (Retail GO, Financials GO, ETF Pro Plus, …) = register it in
tools/enrollment_sources.REGISTRY — THIS job never changes.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

log = logging.getLogger(__name__)
LAST_SENT_KEY = "mfr_toadd_last_sent_date"


class TableSource:
    """An EnrollableSource backed by a table with an add-date per ticker.
    Implements the uniform interface: names_added_on(day) -> set[str]."""

    def __init__(self, name, table, *, ticker_col="ticker", date_col="added_on",
                 where="removed_on IS NULL"):
        self.name, self.table = name, table
        self.ticker_col, self.date_col, self.where = ticker_col, date_col, where

    def names_added_on(self, day) -> set:
        import db_pg
        sql = f"SELECT DISTINCT {self.ticker_col} FROM {self.table} WHERE {self.date_col} = %s"
        if self.where:
            sql += f" AND {self.where}"
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (day,))
            return {r[0].upper() for r in cur.fetchall() if r[0]}

    def current_names(self) -> set:
        """All currently-on tickers (for the backlog sweep) — WHERE clause only, no date."""
        import db_pg
        sql = f"SELECT DISTINCT {self.ticker_col} FROM {self.table}"
        if self.where:
            sql += f" WHERE {self.where}"
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
            return {r[0].upper() for r in cur.fetchall() if r[0]}


class BookSource:
    """EnrollableSource over the Fidelity book (book_positions). Current names = the
    latest snapshot's non-cash underlyings (options resolve to the underlying); a name
    is 'added' on the day it FIRST appears in any snapshot. So held book holdings feed
    the MFR enrollment backlog just like a roster does — cash/money-market excluded."""

    name = "book"
    _BASE = ("FROM book_positions WHERE asset_class <> 'cash' "
             "AND COALESCE(quantity, 0) <> 0")

    def current_names(self) -> set:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT underlying {self._BASE} "
                "AND snapshot_date = (SELECT max(snapshot_date) FROM book_positions)")
            return {r[0].upper() for r in cur.fetchall() if r[0]}

    def names_added_on(self, day) -> set:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT underlying {self._BASE} "
                "GROUP BY underlying HAVING min(snapshot_date) = %s", (day,))
            return {r[0].upper() for r in cur.fetchall() if r[0]}


def _today_et() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return datetime.utcnow().date()


def _mfr_active() -> set:
    import mfr_client
    return {t.upper() for t in (mfr_client.list_watchlist() or [])}


def _set_state(key, value):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO bot_state (key, value, updated_at) VALUES (%s, %s, NOW()) "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
                    (key, value))
        conn.commit()


def _get_state(key):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
        r = cur.fetchone()
    return r[0] if r and r[0] else None


def compile_to_add(day=None) -> dict:
    """Read-only. Union today's adds across all registered sources, drop names already
    active in MFR and known-uncoverable ones. Returns a summary dict (no Telegram)."""
    from tools.enrollment_sources import REGISTRY, KNOWN_UNCOVERABLE
    day = day or _today_et()
    added, per_source = set(), {}
    for src in REGISTRY:
        try:
            s = src.names_added_on(day)
        except Exception as e:
            log.warning("enroll source %s failed: %s", getattr(src, "name", "?"), e)
            s = set()
        per_source[src.name] = s
        added |= s
    active = _mfr_active()
    to_add = sorted((added - active) - set(KNOWN_UNCOVERABLE))
    return {"day": str(day), "to_add": to_add,
            "per_source": {k: sorted(v) for k, v in per_source.items()},
            "added_count": len(added), "active_count": len(active)}


def run_nightly() -> str:
    """Compile + Telegram the to-add list. Once/night (bot_state throttle); quiet when
    there's nothing to add. Returns a status string. No write to MFR."""
    today = str(_today_et())
    if _get_state(LAST_SENT_KEY) == today:
        return "skip:already-sent-today"
    r = compile_to_add()
    if not r["to_add"]:
        _set_state(LAST_SENT_KEY, today)  # mark done so we stay quiet the rest of the night
        return "skip:nothing-to-add"
    prov = ", ".join(f"{k}={len(v)}" for k, v in r["per_source"].items() if v)
    msg = (f"🆕 MFR to-add ({len(r['to_add'])}) [{prov}]:\n" + " ".join(r["to_add"])
           + "\n(paste into MFR → Activate Assets)")
    try:
        from notifier import send_telegram
        send_telegram("MFR to-add", msg, priority=1)
    except Exception as e:
        log.warning("mfr to-add send failed: %s", e)
        return f"error:{e}"
    _set_state(LAST_SENT_KEY, today)
    return f"sent:{len(r['to_add'])}"


# ─────────────────────────── Backlog sweep (full catch-up) ───────────────────────────
# Weekly + on-demand: ALL roster names across sources not yet active in MFR (not just
# today's adds). Read-only. A lightweight "persisted" guard tracks weeks-seen per
# un-enrolled ticker so the weekly sweep flags long-stale names instead of silently
# re-listing them.

BACKLOG_WEEK_KEY = "mfr_backlog_last_week"   # ISO "YYYY-Www" — once/week throttle
BACKLOG_SEEN_KEY = "mfr_backlog_seen"        # JSON {ticker: weeks_seen_unenrolled}
PERSIST_WEEKS = 3


def _iso_week(d=None) -> str:
    d = d or _today_et()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _load_seen() -> dict:
    v = _get_state(BACKLOG_SEEN_KEY)
    if not v:
        return {}
    try:
        return json.loads(v)
    except Exception:
        return {}


def compile_backlog() -> dict:
    """Read-only. ALL current members across EVERY signal source (tools.source_registry:
    etfpro / portsol / ideas / keiths / sigstr / posmon / book) that are NOT active in
    MFR, minus KNOWN_UNCOVERABLE. The full catch-up set. Diffing the canonical universe
    (not just signal_strength + book) means names like the Portfolio Solutions holding
    PAVE are no longer invisible to enrollment."""
    from tools.source_registry import full_universe
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE
    fu = full_universe()
    full = fu["universe"]
    active = _mfr_active()
    to_add = sorted((full - active) - set(KNOWN_UNCOVERABLE) - set(PARKED_FOR_SOURCE))
    return {"to_add": to_add, "per_source": fu["per_source"],
            "full_count": len(full), "active_count": len(active)}


def run_weekly_backlog() -> str:
    """Once/ISO-week. Compile the backlog, bump weeks-seen per still-un-enrolled ticker
    (drop ones that cleared), and Telegram the list — flagging names persisted
    >= PERSIST_WEEKS separately ('enroll or dismiss') rather than silently re-listing.
    Quiet when the backlog is clear. No MFR write."""
    wk = _iso_week()
    if _get_state(BACKLOG_WEEK_KEY) == wk:
        return "skip:already-swept-this-week"
    r = compile_backlog()
    to_add = r["to_add"]
    seen = _load_seen()
    new_seen = {t: int(seen.get(t, 0)) + 1 for t in to_add}   # +1 for still-listed; cleared ones drop
    _set_state(BACKLOG_SEEN_KEY, json.dumps(new_seen))
    _set_state(BACKLOG_WEEK_KEY, wk)
    if not to_add:
        return "skip:backlog-clear"
    persisted = [t for t in to_add if new_seen[t] >= PERSIST_WEEKS]
    fresh = [t for t in to_add if t not in persisted]
    prov = ", ".join(f"{k}={len(v)}" for k, v in r["per_source"].items() if v)
    lines = [f"🧹 MFR backlog ({len(to_add)}) [{prov}] — roster names not yet active in MFR:"]
    if fresh:
        lines.append(" ".join(fresh))
    if persisted:
        lines.append(f"⚠️ persisted ≥{PERSIST_WEEKS} wks — ENROLL or DISMISS "
                     f"(add to KNOWN_UNCOVERABLE): " + " ".join(persisted))
    lines.append("(paste names into MFR → Activate Assets)")
    try:
        from notifier import send_telegram
        send_telegram("MFR backlog", "\n".join(lines) + dark_footer(), priority=1)
    except Exception as e:
        log.warning("backlog send failed: %s", e)
        return f"error:{e}"
    return f"sent:{len(to_add)}(persisted={len(persisted)})"


# ─────────────────────────── DARK footer (enrollment-gap reminder) ──────────────────
# STANDING RULE: every MFR-context Telegram command appends the live "not yet enrolled"
# set so the gap stays visible until it's closed. Distinct from the backlog to-add list:
# the footer is the SCREENER truth — every held/tagged name that currently has no MFR
# range — computed live, book AND tagged, read-only.

def live_dark_names() -> dict:
    """v_screener names with NO MFR range, split into book holdings and tagged-only.
    Excludes KNOWN_UNCOVERABLE (foreign/untradeable) and PARKED_FOR_SOURCE (crypto ->
    btcquant) so the footer nags only about genuinely-enrollable gaps. Read-only."""
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE
    skip = set(KNOWN_UNCOVERABLE) | set(PARKED_FOR_SOURCE)
    import db_pg
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, held FROM v_screener WHERE NOT has_range ORDER BY ticker")
            rows = cur.fetchall()
    except Exception as e:
        log.warning("live_dark_names failed: %s", e)
        return {"book": [], "tagged": []}
    return {"book":   [t for t, held in rows if held and t not in skip],
            "tagged": [t for t, held in rows if not held and t not in skip]}


def dark_footer() -> str:
    """The 'not yet enrolled' footer appended to every MFR command. Returns '' only if
    nothing is dark (rare)."""
    d = live_dark_names()
    book, tagged = d["book"], d["tagged"]
    if not book and not tagged:
        return "\n\n🌑 Not yet enrolled: none — every held/tagged name has an MFR range."
    lines = [f"\n\n🌑 Not yet enrolled — no MFR range ({len(book) + len(tagged)}):"]
    if book:
        lines.append(f"  📗 book ({len(book)}): " + " ".join(book))
    if tagged:
        lines.append(f"  🏷 tagged ({len(tagged)}): " + " ".join(tagged))
    lines.append("  → enroll on the MFR site; ranges arrive via the nightly fan-out.")
    return "\n".join(lines)


def handle_backlog_command(text: str):
    """On-demand Telegram trigger: 'MFR BACKLOG' / '/mfrbacklog' -> reply with the full
    backlog now (read-only; no throttle, no weeks-seen bump). Always appends the live
    DARK 'not yet enrolled' footer. Returns None if not the command so the listener
    falls through to normal handling."""
    if not text or text.strip().upper() not in ("MFR BACKLOG", "/MFRBACKLOG"):
        return None
    r = compile_backlog()
    to_add = r["to_add"]
    if not to_add:
        return ("✅ MFR backlog clear — every roster name is active in MFR "
                "(excl. known-uncoverable)." + dark_footer())
    seen = _load_seen()
    persisted = [t for t in to_add if int(seen.get(t, 0)) >= PERSIST_WEEKS]
    prov = ", ".join(f"{k}={len(v)}" for k, v in r["per_source"].items() if v)
    lines = [f"🧹 MFR backlog ({len(to_add)}) [{prov}]:", " ".join(to_add)]
    if persisted:
        lines.append(f"⚠️ persisted ≥{PERSIST_WEEKS} wks: " + " ".join(persisted))
    lines.append("(paste into MFR → Activate Assets)")
    return "\n".join(lines) + dark_footer()
