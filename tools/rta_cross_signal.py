"""
rta_cross_signal.py — RTA → book/SS matching + same-day close suppression.

Operator spec (2026-07-11): Real-Time Alerts are direction/sector/factor
tells. When one lands, Python matches the name against the operator's book
(direct holding, wrapper linkage, shared sector) and the Signal Strength
roster, and sends ONE fact-based Telegram alert — matched names, sides, range
positions, and a pointer at the operator's own rulebook. Never advice.

Close-type RTAs ('sell'/'cover' — NOT sell-some/cover-some trims) also write
rta_position_closes, which same-day-suppresses the name in both alert
universes (polling_universe + price_monitor). Next publication cycle governs
from tomorrow.

Python owns all matching; no LLM. Failures are loud in the returned summary
and the log — a cross-signal failure never blocks the RTA upsert itself.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("rta_cross_signal")

# Full closes only — the -SOME gradations are trims, not closes.
CLOSE_TYPES = {"sell", "cover"}

# Buy-flavored actions frame matches as scale-in checks; sell-flavored as
# take-profit checks. (BUY covers count as bullish tells; trims as bearish.)
_BULLISH = {"buy", "cover", "cover-some", "add"}
_BEARISH = {"sell", "sell-some", "short", "trim"}


# ─────────────────────────── DB lookups (guarded) ───────────────────────────

def _sector_of(ticker: str):
    """(gics_sector, subsector) from ticker_tags, (None, None) if untagged."""
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT gics_sector, subsector FROM ticker_tags "
                        "WHERE ticker = %s", (ticker,))
            r = cur.fetchone()
            return (r[0], r[1]) if r else (None, None)
    except Exception as e:
        log.warning("sector lookup failed for %s: %s", ticker, e)
        return (None, None)


def _sector_members_in_book(gics: str, book: set) -> dict:
    """{book_ticker: subsector} sharing the RTA name's GICS sector."""
    if not gics or not book:
        return {}
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT ticker, subsector FROM ticker_tags "
                        "WHERE gics_sector = %s AND ticker = ANY(%s)",
                        (gics, sorted(book)))
            return {t: sub for t, sub in cur.fetchall()}
    except Exception as e:
        log.warning("sector-member lookup failed for %s: %s", gics, e)
        return {}


def _rp_for(tickers) -> dict:
    """{ticker: range_pos 0..1} from latest mfr_snapshots. Missing -> absent."""
    tickers = [t for t in set(tickers) if t]
    if not tickers:
        return {}
    out = {}
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (ticker) ticker, "
                "(price - range_low) / NULLIF(range_high - range_low, 0) "
                "FROM mfr_snapshots WHERE ticker = ANY(%s) "
                "ORDER BY ticker, snapshot_date DESC", (tickers,))
            for t, rp in cur.fetchall():
                if rp is not None:
                    out[t] = float(rp)
    except Exception as e:
        log.warning("rp lookup failed: %s", e)
    return out


def record_close(ticker: str, closed_on: date, signal_type: str,
                 source_email_id: str | None) -> bool:
    """Write the same-day suppression row. True on success."""
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(
                "INSERT INTO rta_position_closes "
                "(ticker, closed_on, signal_type, source_email_id) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (ticker, closed_on) DO NOTHING",
                (ticker, closed_on, signal_type, source_email_id))
            c.commit()
        return True
    except Exception as e:
        log.warning("record_close failed for %s: %s", ticker, e)
        return False


def closed_today() -> set:
    """Tickers suppressed for today by a close-type RTA. Empty set + loud log
    on failure (missing suppression = extra alerts, never lost data)."""
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT ticker FROM rta_position_closes "
                        "WHERE closed_on = CURRENT_DATE")
            return {r[0] for r in cur.fetchall()}
    except Exception as e:
        log.warning("closed_today lookup failed (no suppression applied): %s", e)
        return set()


# ─────────────────────────── Matching (pure over inputs) ───────────────────────────

def build_matches(rec: dict, sides: dict, links: dict, ss_members: set,
                  sector_pair=None, sector_book=None, rps=None) -> dict:
    """Pure matcher — all DB data passed in, unit-testable.
    Returns {direct, wrappers[], sector[], on_ss, gics, subsector}."""
    t = (rec.get("ticker") or "").upper()
    rps = rps or {}
    gics, subsector = sector_pair or (None, None)
    out = {"direct": None, "wrappers": [], "sector": [], "on_ss": t in ss_members,
           "gics": gics, "subsector": subsector}

    s = sides.get(t)
    if s and s.get("side") in ("long", "short"):
        out["direct"] = {"ticker": t, "side": s["side"],
                         "raw_side": s.get("raw_side"), "rp": rps.get(t)}

    for w, lk in (links or {}).items():
        if (lk.get("underlying") or "").upper() == t:
            ws = sides.get(w)
            if ws and ws.get("side") in ("long", "short"):
                out["wrappers"].append({"ticker": w, "side": ws["side"],
                                        "inverse": bool(lk.get("inverse")),
                                        "rp": rps.get(w)})

    for bt, sub in sorted((sector_book or {}).items()):
        if bt == t:
            continue
        bs = sides.get(bt)
        if bs and bs.get("side") in ("long", "short"):
            out["sector"].append({"ticker": bt, "side": bs["side"],
                                  "subsector": sub, "rp": rps.get(bt)})
    return out


def format_alert(rec: dict, m: dict, closed: bool) -> tuple[str, str]:
    """(title, body) — facts only; the rulebook line names the check, the
    operator decides."""
    t = (rec.get("ticker") or "").upper()
    sig = (rec.get("signal_type") or "?").lower()
    side = rec.get("side") or "?"
    analyst = rec.get("analyst")
    note = rec.get("note")
    price = rec.get("price")

    bullish = sig in _BULLISH
    check = "scale-in check" if bullish else "take-profit check"

    title = f"🔁 RTA {sig.upper()} {t}"
    lines = []
    head = f"Keith{'/' + analyst if analyst else ''}: {sig.upper()} {t} ({side})"
    if price:
        head += f" @ ${price}"
    if note:
        head += f" — “{note}”"
    lines.append(head)
    if m.get("gics"):
        lines.append(f"sector: {m['gics']}"
                     + (f" · {m['subsector']}" if m.get("subsector") else ""))
    if m.get("on_ss"):
        lines.append("on Signal Strength roster")

    def _rp(v):
        return f" rp={v:.2f}" if isinstance(v, float) else ""

    if m.get("direct"):
        d = m["direct"]
        lines.append(f"📗 YOU HOLD {d['ticker']}: {d['side'].upper()}{_rp(d['rp'])}"
                     f" — {check} per your rules")
    for w in m.get("wrappers", []):
        inv = " ↯inv" if w["inverse"] else ""
        lines.append(f"📗 EXPOSURE via {w['ticker']} ({w['side']} {t}{inv})"
                     f"{_rp(w['rp'])} — {check}")
    if m.get("sector"):
        names = ", ".join(f"{x['ticker']}({x['side']}{_rp(x['rp']).strip()})"
                          for x in m["sector"][:8])
        more = len(m["sector"]) - 8
        lines.append(f"same-sector in book: {names}"
                     + (f" +{more} more" if more > 0 else ""))
    if closed:
        lines.append(f"⛔ {t} removed from today's alert universe "
                     f"(close-type RTA; publications govern from tomorrow)")
    return title, "\n".join(lines)


# ─────────────────────────── Entry point (called by parser_rta) ───────────────────────────

def handle_rta(rec: dict, signal_date: date, message_id: str | None) -> dict:
    """Match, notify, and (for closes) suppress. Loud dict summary back to the
    parser; never raises into the parser's upsert path."""
    out = {"matched": 0, "closed": False, "sent": False}
    try:
        t = (rec.get("ticker") or "").upper()
        if not t:
            out["error"] = "no ticker"
            return out

        sig = (rec.get("signal_type") or "").lower()
        closed = sig in CLOSE_TYPES
        if closed:
            out["closed"] = record_close(t, signal_date, sig, message_id)

        from tools.book_direction import book_sides
        try:
            sides = book_sides()
        except Exception as e:
            log.warning("cross-signal: book sides unavailable: %s", e)
            sides = {}
        try:
            from tools.wrapper_links import get_links
            links = get_links()
        except Exception as e:
            log.warning("cross-signal: wrapper links unavailable: %s", e)
            links = {}
        try:
            from tools.source_registry import BY_TAG
            ss_members = BY_TAG["sigstr"].members()
        except Exception as e:
            log.warning("cross-signal: SS members unavailable: %s", e)
            ss_members = set()

        sector_pair = _sector_of(t)
        book = {k for k, v in sides.items() if v.get("side") in ("long", "short")}
        sector_book = _sector_members_in_book(sector_pair[0], book)
        rp_targets = [t] + list(sector_book) + [w for w, lk in links.items()
                                               if (lk.get("underlying") or "").upper() == t]
        rps = _rp_for(rp_targets)

        m = build_matches(rec, sides, links, ss_members,
                          sector_pair=sector_pair, sector_book=sector_book,
                          rps=rps)
        out["matched"] = (1 if m["direct"] else 0) + len(m["wrappers"]) + len(m["sector"])

        # Send when there's something to say: any book relevance, or a close
        # (the removal itself is operator-relevant). Pure no-match buys stay
        # quiet — the RTA row is stored regardless.
        if out["matched"] or closed or m["on_ss"]:
            title, body = format_alert(rec, m, closed and out["closed"])
            try:
                from notifier import send_telegram
                send_telegram(title, body)
                out["sent"] = True
            except Exception as e:
                out["error"] = f"telegram send failed: {e}"
                log.warning("cross-signal telegram failed for %s: %s", t, e)
    except Exception as e:
        out["error"] = str(e)
        log.exception("cross-signal failed")
    return out
