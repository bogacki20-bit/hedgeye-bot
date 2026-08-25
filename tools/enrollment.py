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
import re as _re
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


# ─────────────────── watchlist sanity guard (the 7/20 fix) ──────────────────
# 2026-07-20: MFR BACKLOG emitted 456 names; 439 were ALREADY activated. Cause:
# mfr_client.list_watchlist() returned [] (auth/timeout/shape), so the diff
# subtracted nothing and re-flagged the entire feed universe as "missing". The
# guard was written up that day and never built — this is it.
#
# Rule: the backlog is a SUBTRACTION. If the thing being subtracted is empty or
# has collapsed, the answer is garbage, and garbage that looks authoritative is
# worse than no answer. Refuse and say why.

class WatchlistUnavailable(RuntimeError):
    """MFR's watchlist read is empty or implausibly small — the backlog diff
    would be meaningless, so it is not computed at all."""


WATCHLIST_GOOD_KEY = "mfr_watchlist_last_good_count"
COLLAPSE_FLOOR = 0.6        # refuse below 60% of the high-water count
BLOCKED_NOTIFY_KEY = "mfr_enroll_blocked_notified_on"   # one refusal ping per day


def watchlist_verdict(count, last_good, floor=COLLAPSE_FLOOR, served=0) -> tuple:
    """Pure. (ok, reason).

    count     — names the watchlist read just returned
    last_good — previous believable count (0 when unknown)
    served    — DISTINCT tickers MFR has actually served ranges for recently

    Three checks, cheapest first. `served` is the important one: it needs no
    stored history, so it works on the very first run after a deploy — which is
    exactly when a stored baseline is 0 and would rubber-stamp a bad read.

    It is also self-evidently sound. MFR cannot serve daily ranges for a ticker
    that isn't activated in the account. So if the range feed is delivering ~560
    distinct names while the LIST call claims 88, the list call is wrong — no
    history, no operator confirmation, no ambiguity. That is precisely the
    2026-07-30 reading: 472 'un-enrolled' names while only 2 names in the whole
    book lacked a range row."""
    if not count:
        return False, ("the MFR watchlist read returned 0 names. That is an API "
                       "failure (auth / timeout / changed response shape), not an "
                       "empty account — every name would look un-enrolled. This is "
                       "exactly the 2026-07-20 '456 backlog' failure.")
    if served and count < served * floor:
        return False, (f"the MFR watchlist read returned {count} names, but MFR has "
                       f"served ranges for {served} distinct tickers in the last "
                       f"{SERVED_WINDOW_DAYS} days. It cannot serve a range for a "
                       f"ticker that is not activated, so the LIST call is returning "
                       f"a partial result (pagination? truncation?) — the backlog "
                       f"would flag ~{max(0, served - count)} already-active names.")
    if last_good and count < last_good * floor:
        return False, (f"the MFR watchlist collapsed to {count} names from a "
                       f"last-known-good {last_good} (under {floor:.0%}). A partial "
                       f"read would flag hundreds of already-activated names.")
    return True, ""


SERVED_WINDOW_DAYS = 7


def served_ticker_count(days=SERVED_WINDOW_DAYS) -> int:
    """DISTINCT tickers MFR actually returned range data for recently. A lower
    bound on the true activated set, derived from the range feed rather than from
    the list endpoint — so it stays honest when the list endpoint doesn't.
    Returns 0 (guard disabled) if the query fails; never raises."""
    import db_pg
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(DISTINCT ticker) FROM mfr_snapshots "
                        "WHERE snapshot_date >= CURRENT_DATE - %s", (days,))
            r = cur.fetchone()
        return int(r[0]) if r and r[0] else 0
    except Exception as e:
        log.warning("served-ticker cross-check unavailable: %s", e)
        return 0


def active_watchlist(force=False) -> set:
    """The MFR active set, sanity-checked. Raises WatchlistUnavailable unless
    `force`. NOT read-only: maintains the high-water count in bot_state.

    The baseline is a HIGH-WATER MARK, deliberately. An auto-ratcheting baseline
    walks itself down under repeated partial reads — 560→340 passes the 60% floor,
    then 340→205 passes, then 205→123 — and three degraded reads later a 130-name
    watchlist is 'believable' and the backlog lies exactly the way it did on 7/20.
    So the baseline only rises on its own.

    A real shrink (the operator prunes MFR) therefore blocks once, on purpose.
    `force` is the acknowledgement: it accepts the read AND resets the high-water
    mark to it, so the block clears for the nightly and weekly jobs too rather
    than wedging them until the watchlist grows back."""
    active = _mfr_active()
    n = len(active)
    raw = _get_state(WATCHLIST_GOOD_KEY)
    try:
        high_water = int(raw) if raw else 0
    except (TypeError, ValueError):
        high_water = 0
    ok, reason = watchlist_verdict(n, high_water, served=served_ticker_count())
    if not ok:
        if not force:
            raise WatchlistUnavailable(reason)
        log.warning("watchlist guard BYPASSED (force): %s", reason)
        if n:                       # forced acceptance rebases; empty never does
            _set_state(WATCHLIST_GOOD_KEY, str(n))
        return active
    if n > high_water:
        _set_state(WATCHLIST_GOOD_KEY, str(n))
    return active


def _notify_blocked(context: str, reason: str) -> None:
    """One refusal ping per calendar day across ALL enrollment jobs. The weekly
    loop re-enters every 30 min on Sunday evening and the nightly every tick, so
    an unthrottled refusal is 10 identical priority-2 alarms a night — which is
    how a real alert gets trained into noise."""
    today = str(_today_et())
    if _get_state(BLOCKED_NOTIFY_KEY) == today:
        return
    try:
        from notifier import send_telegram
        sent = send_telegram("MFR enrollment",
                      f"🛑 {context} NOT computed — {reason}\n"
                      f"No list was sent because it would have been wrong. Check "
                      f"MFR_API_TOKEN and the /v2/asset list call. bot_state: "
                      f"mfr_watchlist_last_count / _last_error / _last_ok_at.\n"
                      f"If the watchlist really did shrink, run: MFR BACKLOG FORCE "
                             f"— that accepts the new size and unblocks the "
                             f"scheduled jobs.", priority=2)
    except Exception as e:
        log.warning("blocked-notify send failed: %s", e)
        sent = False
    if sent is not False:       # stamp only on success, else the day goes silent
        _set_state(BLOCKED_NOTIFY_KEY, today)


TG_CHUNK = 3800     # Telegram hard-caps at 4096; notifier.send_telegram adds a title


def _send_chunked(title: str, body: str) -> bool:
    """notifier.send_telegram posts raw and swallows a 400 — so a backlog longer
    than Telegram's 4096 limit vanishes and the caller still reports 'sent'.
    Split on line boundaries (never mid-ticker) and require EVERY part to land."""
    from notifier import send_telegram
    parts, cur = [], ""
    for ln in body.split("\n"):
        while len(ln) > TG_CHUNK:                     # one enormous ticker line
            cut = ln.rfind(" ", 0, TG_CHUNK)
            cut = cut if cut > 0 else TG_CHUNK
            parts.append(ln[:cut])
            ln = ln[cut:].lstrip()
        if len(cur) + len(ln) + 1 > TG_CHUNK:
            parts.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        parts.append(cur)
    ok = True
    for i, p in enumerate(parts, 1):
        tag = f"{title} ({i}/{len(parts)})" if len(parts) > 1 else title
        ok = send_telegram(tag, p, priority=1) is not False and ok
    return ok


# ─────────────────────────── provenance (who put this name here) ────────────
# The backlog printed a flat blob plus per-source COUNTS, so an unfamiliar ticker
# had no explanation. These group it by the exact feed combination that claims it.

def group_by_origin(backlog, per_source) -> dict:
    """Pure. {'feed+feed': [tickers]} — the exact combination of source tags that
    put each backlogged name in the universe. '<none>' means no feed claims it,
    which should be impossible and is worth shouting about."""
    origin: dict = {}
    for tag, members in (per_source or {}).items():
        for t in members:
            origin.setdefault(t, set()).add(tag)
    groups: dict = {}
    for t in backlog:
        key = "+".join(sorted(origin.get(t, {"<none>"})))
        groups.setdefault(key, []).append(t)
    return {k: sorted(v) for k, v in groups.items()}


# Two-letter codes so a per-ticker tag stays readable at 300 names. Any source
# not listed falls back to its own tag, so a new feed is never silently untagged.
SRC_TAG = {
    "etfpro": "ep", "portsol": "ps", "ideas": "ii", "keiths": "kt",
    "sigstr": "ss", "finsigstr": "fs", "posmon": "pm", "book": "bk",
    "btcquant": "bq", "riskrange": "rr", "rta": "rt", "sigchange": "sc",
    "portactions": "pa", "iichanges": "ic", "hedgai": "ha", "momo": "mo",
    "retail": "rl",
}
TAG_LEGEND = ("ep=ETF Pro · ps=PortSol · ii=Ideas · kt=Keith's · ss=SigStr · "
              "fs=FinSigStr · pm=PosMon · bk=book · bq=BTCQuant · rr=RiskRange "
              "· rt=RTA · sc=SigChange · pa=PSActions · ic=IIChanges · "
              "ha=HedgAI · mo=MOMO · rl=Retail")


def tagged_list(backlog, per_source, held_fills=None) -> str:
    """Pure. 'AAPL(rr,pm) BBRE(bk) BEEN(ss)' — every ticker carries the feed(s)
    that put it on the backlog, so an unfamiliar name explains itself in place
    rather than in a separate block.

    held_fills ({ticker: fill_pct_or_None}) appends 'held N%fill' to a name
    already in the book, so a held position is never read as a new idea —
    same annotation the report's CANDIDATES block carries.

    NOT pasteable — the tags are inside the token. The untagged list is printed
    separately for that; a tagged list pasted into MFR would enroll nothing."""
    origin: dict = {}
    for tag, members in (per_source or {}).items():
        for t in members:
            origin.setdefault(t, set()).add(SRC_TAG.get(tag, tag))
    held_fills = held_fills or {}
    parts = []
    for t in backlog:
        tags = sorted(origin.get(t, {"?"}))
        if t in held_fills:
            f = held_fills[t]
            tags.append(f"held {f:.0f}%fill" if f is not None else "held")
        parts.append(f"{t}({','.join(tags)})")
    return " ".join(parts)


def origin_summary(backlog, per_source, top=6) -> list:
    """Pure. COUNTS per origin combination — no ticker names, so it can never
    crowd out or truncate the paste list. `MFR BACKLOG WHY` prints the names."""
    groups = group_by_origin(backlog, per_source)
    if not groups:
        return []
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    parts = [f"{combo}={len(ts)}" for combo, ts in ranked[:top]]
    tail = sum(len(ts) for _, ts in ranked[top:])
    if tail:
        parts.append(f"+{len(ranked) - top} smaller combos={tail}")
    out = ["origin: " + " · ".join(parts) + "   (MFR BACKLOG WHY = names per feed)"]
    if "<none>" in groups:
        out.append("⚠️ '<none>' = on the backlog with no feed claiming it — "
                   "that should be impossible. Do not enroll those blind.")
    return out


def provenance_lines(backlog, per_source, cap_per_group=60) -> list:
    """Pure. The detailed WHY view: biggest origin group first, names capped per
    group with a loud '+N more'.

    NOT used for the default backlog reply. This view IS lossy above the cap, so
    it must never carry the 'paste into MFR' instruction — the flat, complete
    to_add list does that job (a truncated paste list means a half-enrolled
    account that reads as finished)."""
    groups = group_by_origin(backlog, per_source)
    out = []
    for combo, ts in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        shown = ts[:cap_per_group]
        more = f" +{len(ts) - cap_per_group} more" if len(ts) > cap_per_group else ""
        out.append(f"• [{combo}] {len(ts)}: " + " ".join(shown) + more)
    if "<none>" in groups:
        out.append("⚠️ '<none>' = on the backlog with no feed claiming it — "
                   "that should be impossible. Do not enroll those blind.")
    return out


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


def compile_to_add(day=None, force=False) -> dict:
    """Read-only. Union today's adds across all registered sources, drop names already
    active in MFR and known-uncoverable ones. Returns a summary dict (no Telegram).
    Raises WatchlistUnavailable when the MFR read can't be trusted (see the guard)."""
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
    active = active_watchlist(force=force)
    raw = sorted((added - active) - set(KNOWN_UNCOVERABLE))
    v = validate_backlog_symbols(raw)   # same gate as the backlog — the nightly
    return {"day": str(day), "to_add": v["kept"],   # paste line feeds MFR too
            "per_source": {k: sorted(v2) for k, v2 in per_source.items()},
            "added_count": len(added), "active_count": len(active),
            "dropped": sorted(v["dropped_shape"] + v["dropped_quote"]),
            "validation_note": v["note"]}


def run_nightly() -> str:
    """Compile + Telegram the to-add list. Once/night (bot_state throttle); quiet when
    there's nothing to add. Returns a status string. No write to MFR."""
    today = str(_today_et())
    if _get_state(LAST_SENT_KEY) == today:
        return "skip:already-sent-today"
    # One mfr_backlog_snapshots row per day (078) so MFR COVERAGE can print a
    # delta against yesterday even on days the operator never asks. Best-effort
    # — the to-add batch must go out whether or not the snapshot lands.
    try:
        from tools.mfr_coverage import record_daily_snapshot
        log.info("backlog snapshot: %s", record_daily_snapshot())
    except Exception as e:
        log.warning("backlog snapshot failed: %s", e)
    try:
        r = compile_to_add()
    except WatchlistUnavailable as e:
        # Do NOT mark the day done — retry on the next tick once MFR recovers.
        # A collapsed (non-empty) read is silent at the mfr_client layer, so this
        # is the only place the operator hears about it.
        log.error("nightly to-add blocked: %s", e)
        _notify_blocked("Nightly MFR to-add", str(e))
        return f"blocked:watchlist-unavailable ({e})"
    if not r["to_add"]:
        if r.get("dropped"):
            # Every name today failed validation — that IS the story. Staying
            # quiet here would make the gate a silent shredder.
            try:
                from notifier import send_telegram
                send_telegram("MFR to-add",
                              f"🗑 MFR to-add: all {len(r['dropped'])} of "
                              f"today's names failed validation (malformed or "
                              f"no live quote): " + " ".join(r["dropped"]),
                              priority=1)
            except Exception as e:
                log.warning("mfr to-add drop notice failed: %s", e)
        _set_state(LAST_SENT_KEY, today)  # mark done so we stay quiet the rest of the night
        return ("skip:all-dropped-by-validation" if r.get("dropped")
                else "skip:nothing-to-add")
    prov = ", ".join(f"{k}={len(v)}" for k, v in r["per_source"].items() if v)
    msg = (f"🆕 MFR to-add ({len(r['to_add'])} · dropped "
           f"{len(r.get('dropped', []))} unresolvable) [{prov}]:\n"
           + " ".join(r["to_add"])
           + (("\n🗑 dropped: " + " ".join(r["dropped"])) if r.get("dropped") else "")
           + ((f"\n⚠️ {r['validation_note']}") if r.get("validation_note") else "")
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


# ─────────────────────────── symbol validation (backlog gate) ───────────────
# The backlog is a pass-through: whatever the source tables hold is what gets
# printed, and several upstream parsers can seed garbage (suffix fragments
# like 'RPI'/'L' from a split on '.', pure-numeric OCR tokens, raw Fidelity
# option strings stored as underlyings). KNOWN_UNCOVERABLE is a hand-curated
# band-aid that only blocks garbage AFTER someone saw it. This gate is the
# structural fix: a symbol prints only if it is shaped like an instrument AND
# resolves against a live quote source. Dropped symbols are logged and
# counted in the output — never silently discarded, never printed.

# Instrument shape: 1-7 alnum chars, up to two suffix groups joined by . _ -
# (RPI.L, 005930.KS, ES_F, VOLV-B.ST), and at least one letter somewhere —
# a pure-numeric token ('2513', '100.00') is an OCR artifact, not a ticker.
_SYMBOL_SHAPE_RE = _re.compile(r"^[A-Z0-9]{1,7}(?:[._\-][A-Z0-9]{1,4}){0,2}$")

# Below this fraction of successful quote lookups the feed itself is judged
# degraded and the quote gate FAILS OPEN (shape gate still applies): dropping
# half the backlog because yfinance rate-limited us would be a confident lie,
# the same failure mode the watchlist guard exists to prevent.
QUOTE_RATE_FLOOR = 0.60


def _symbol_shape_ok(t: str) -> bool:
    return bool(_SYMBOL_SHAPE_RE.match(t)) and bool(_re.search(r"[A-Z]", t))


def _yf_symbol_for(t: str) -> str:
    """Backlog symbol -> yfinance symbol. Uses the shared alias map, plus the
    stored futures form X_F -> X=F."""
    try:
        from yfinance_client import _resolve_yf_symbol
        s = _resolve_yf_symbol(t)
    except Exception:
        s = t
    if s == t and t.endswith("_F"):
        s = t[:-2] + "=F"
    return s


def _live_quote_probe(symbols) -> set:
    """One batched yfinance download; returns the subset of `symbols` that came
    back with at least one real close. Raises on total feed failure so the
    caller can fail open. Module-level so tests can monkeypatch it."""
    import yfinance as yf
    m = {t: _yf_symbol_for(t) for t in symbols}
    # 1mo, not 5d: thin OTC names (GRUSF) can miss a week of prints and were
    # false-dropped by the shorter window. One real close in a month is proof
    # of existence, which is all this gate asks.
    data = yf.download(sorted(set(m.values())), period="1mo", interval="1d",
                       group_by="ticker", progress=False, threads=True)
    if data is None or data.empty:
        raise RuntimeError("yfinance returned no data for the whole batch")
    ok = set()
    for t, s in m.items():
        try:
            closes = data[s]["Close"] if len(set(m.values())) > 1 else data["Close"]
            if closes.dropna().shape[0] > 0:
                ok.add(t)
        except Exception:
            pass
    return ok


def validate_backlog_symbols(symbols) -> dict:
    """Gate a backlog list. Returns {kept, dropped_shape, dropped_quote, note}.
    note != '' means the quote gate could not run (or was overridden by the
    degradation floor) and the list is shape-checked only."""
    kept, dropped_shape = [], []
    for t in symbols:
        (kept if _symbol_shape_ok(t) else dropped_shape).append(t)
    for t in dropped_shape:
        log.warning("backlog validation: dropped malformed symbol %r", t)
    dropped_quote, note = [], ""
    if kept:
        try:
            ok = _live_quote_probe(kept)
            if len(ok) < QUOTE_RATE_FLOOR * len(kept):
                note = (f"quote feed degraded ({len(ok)}/{len(kept)} resolved) "
                        f"— quote gate FAILED OPEN, list is shape-checked only")
                log.warning("backlog validation: %s", note)
            else:
                dropped_quote = [t for t in kept if t not in ok]
                kept = [t for t in kept if t in ok]
                for t in dropped_quote:
                    log.warning("backlog validation: dropped unresolvable "
                                "symbol %r (no live quote)", t)
        except Exception as e:
            note = f"live resolution unavailable ({e}) — list is shape-checked only"
            log.warning("backlog validation: %s", note)
    return {"kept": kept, "dropped_shape": dropped_shape,
            "dropped_quote": dropped_quote, "note": note}


def _validation_lines(r) -> list:
    """The dropped/degraded evidence lines both emitters share. Empty list when
    nothing was dropped and the quote gate ran clean — the count in the head
    line already says 'dropped 0'."""
    out = []
    dropped = r.get("dropped", [])
    if dropped:
        out.append("🗑 dropped (not resolvable as instruments — logged, never "
                   "pasteable): " + " ".join(dropped))
    if r.get("validation_note"):
        out.append(f"⚠️ {r['validation_note']}")
    hf = r.get("held_fills") or {}
    if hf:
        out.append("📗 already held (sized positions, not new ideas): "
                   + " ".join(f"{t}({hf[t]:.0f}%fill)" if hf[t] is not None
                              else t for t in sorted(hf)))
    return out


def held_fill_map(tickers) -> dict:
    """{ticker: fill_pct_or_None} for the subset of `tickers` currently in the
    book (latest Fidelity snapshot = source of truth). Same fill math as the
    report's CANDIDATES block (tools.position_targets.compute_fills). Never
    raises — an unreadable book yields {} and the backlog goes out untagged
    rather than not at all."""
    if not tickers:
        return {}
    try:
        import db_pg
        from tools.book_direction import book_sides
        from tools.position_targets import compute_fills
        sides = book_sides()
        want = set(tickers) & set(sides)
        if not want:
            return {}
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            agg = compute_fills(cur, sides).get("agg", {})
        return {t: (agg.get(t) or {}).get("fill") for t in want}
    except Exception as e:
        log.warning("backlog held-fill lookup failed: %s", e)
        return {}


def compile_backlog(force=False) -> dict:
    """ALL current members across EVERY signal source (tools.source_registry:
    etfpro / portsol / ideas / keiths / sigstr / posmon / book) that are NOT active in
    MFR, minus KNOWN_UNCOVERABLE. The full catch-up set. Diffing the canonical universe
    (not just signal_strength + book) means names like the Portfolio Solutions holding
    PAVE are no longer invisible to enrollment.

    Reads only, with ONE exception: the watchlist guard maintains its high-water
    count in bot_state (see active_watchlist). No signal table is ever written.

    Raises WatchlistUnavailable when the MFR read is empty or collapsed — the whole
    computation is a subtraction, so a bad subtrahend yields a confident lie."""
    from tools.source_registry import enrollment_universe
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE
    # ALL-TIME, not current membership (2026-08-01). full_universe() answers
    # "who is on the roster right now" and is right for SCREEN; enrollment wants
    # "what has Hedgeye ever published data on", because range history is only
    # there the day you need it if the name was enrolled long before.
    fu = enrollment_universe()
    full = fu["universe"]
    active = active_watchlist(force=force)
    raw = sorted((full - active) - set(KNOWN_UNCOVERABLE) - set(PARKED_FOR_SOURCE))
    v = validate_backlog_symbols(raw)
    to_add = v["kept"]
    return {"to_add": to_add, "per_source": fu["per_source"],
            "full_count": len(full), "active_count": len(active),
            "forced": bool(force),
            "dropped": sorted(v["dropped_shape"] + v["dropped_quote"]),
            "validation_note": v["note"],
            "held_fills": held_fill_map(to_add)}


def run_weekly_backlog() -> str:
    """Once/ISO-week. Compile the backlog, bump weeks-seen per still-un-enrolled ticker
    (drop ones that cleared), and Telegram the list — flagging names persisted
    >= PERSIST_WEEKS separately ('enroll or dismiss') rather than silently re-listing.
    Quiet when the backlog is clear. No MFR write."""
    wk = _iso_week()
    if _get_state(BACKLOG_WEEK_KEY) == wk:
        return "skip:already-swept-this-week"
    try:
        r = compile_backlog()
    except WatchlistUnavailable as e:
        # Do NOT stamp the week — the sweep hasn't happened.
        log.error("weekly backlog blocked: %s", e)
        _notify_blocked("Weekly MFR backlog", str(e))
        return f"blocked:watchlist-unavailable ({e})"
    to_add = r["to_add"]
    seen = _load_seen()
    new_seen = {t: int(seen.get(t, 0)) + 1 for t in to_add}   # +1 for still-listed; cleared ones drop
    if not to_add:
        _set_state(BACKLOG_SEEN_KEY, json.dumps(new_seen))
        _set_state(BACKLOG_WEEK_KEY, wk)
        return "skip:backlog-clear"
    persisted = [t for t in to_add if new_seen[t] >= PERSIST_WEEKS]
    lines = [f"🧹 MFR backlog ({len(to_add)} of {r['full_count']} universe · "
             f"{r['active_count']} already active · dropped "
             f"{len(r.get('dropped', []))} unresolvable) — roster names not "
             f"yet active:",
             tagged_list(to_add, r["per_source"], r.get("held_fills")),
             TAG_LEGEND]
    lines += origin_summary(to_add, r["per_source"])
    lines += _validation_lines(r)
    lines.append("── paste this line (no tags) ──")
    lines.append(" ".join(to_add))          # COMPLETE list — the paste target
    if persisted:
        lines.append(f"⚠️ persisted ≥{PERSIST_WEEKS} wks — ENROLL or DISMISS "
                     f"(add to KNOWN_UNCOVERABLE): " + " ".join(persisted))
    lines.append("(paste the untagged line into MFR → Activate Assets)")
    try:
        ok = _send_chunked("MFR backlog", "\n".join(lines) + dark_footer())
    except Exception as e:
        log.warning("backlog send failed: %s", e)
        return f"error:{e}"
    if not ok:
        # Week NOT stamped and weeks-seen NOT bumped — the sweep gets another go
        # next tick rather than being marked done on a message that never landed.
        log.error("weekly backlog send incomplete — not marking the week done")
        return "error:send-incomplete"
    _set_state(BACKLOG_SEEN_KEY, json.dumps(new_seen))
    _set_state(BACKLOG_WEEK_KEY, wk)
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

    def _cap(xs, n=60):
        return " ".join(xs[:n]) + (f" +{len(xs) - n} more" if len(xs) > n else "")

    if book:
        lines.append(f"  📗 book ({len(book)}): " + _cap(book))
    if tagged:
        # capped: the footer rides on EVERY MFR command, so an unbounded tagged
        # list would push the real payload past Telegram's limit
        lines.append(f"  🏷 tagged ({len(tagged)}): " + _cap(tagged))
    lines.append("  → enroll on the MFR site; ranges arrive via the nightly fan-out.")
    return "\n".join(lines)


def handle_backlog_command(text: str):
    """On-demand Telegram trigger: 'MFR BACKLOG' / '/mfrbacklog' -> reply with the full
    backlog now (read-only; no throttle, no weeks-seen bump). Always appends the live
    DARK 'not yet enrolled' footer. Returns None if not the command so the listener
    falls through to normal handling."""
    if not text:
        return None
    # Whitespace-normalised so '/mfrbacklog force' and 'MFR  BACKLOG  WHY' both land.
    up = " ".join(text.strip().upper().split()).rstrip(".!?")
    up = up.replace("/MFRBACKLOG", "MFR BACKLOG")
    if not up.startswith("MFR BACKLOG"):
        return None
    args = up[len("MFR BACKLOG"):].split()
    if any(a not in ("FORCE", "WHY") for a in args):
        return (f"🛑 MFR BACKLOG: unknown option {' '.join(args)!r} — use "
                f"MFR BACKLOG · MFR BACKLOG WHY · MFR BACKLOG FORCE")
    force, why = "FORCE" in args, "WHY" in args
    try:
        r = compile_backlog(force=force)
    except WatchlistUnavailable as e:
        return (f"🛑 MFR BACKLOG not computed — {e}\n"
                f"Nothing is listed because the list would be wrong. Check "
                f"MFR_API_TOKEN and the /v2/asset list call; bot_state keys "
                f"mfr_watchlist_last_count / _last_error / _last_ok_at hold the "
                f"last read.\nIf the watchlist really did shrink, MFR BACKLOG "
                f"FORCE accepts the new size and unblocks the scheduled jobs too."
                + dark_footer())
    to_add = r["to_add"]
    if not to_add:
        return ("✅ MFR backlog clear — every roster name is active in MFR "
                f"({r['active_count']} active, excl. known-uncoverable)."
                + dark_footer())
    seen = _load_seen()
    persisted = [t for t in to_add if int(seen.get(t, 0)) >= PERSIST_WEEKS]
    dropped = r.get("dropped", [])
    head = (f"🧹 MFR backlog ({len(to_add)} of {r['full_count']} universe · "
            f"{r['active_count']} already active · dropped {len(dropped)} "
            f"unresolvable)"
            + (" ⚠FORCED — guard bypassed" if force else ""))
    if why:
        # Detail view: names per feed. Lossy above the per-group cap, so it does
        # NOT claim to be a paste list — plain MFR BACKLOG is.
        lines = [head + " — which feed put each name here:"]
        lines += provenance_lines(to_add, r["per_source"])
        lines += _validation_lines(r)
        lines.append("(explanatory view — paste from plain MFR BACKLOG, "
                     "which prints the complete list)")
        return "\n".join(lines) + dark_footer()
    lines = [head + ":",
             tagged_list(to_add, r["per_source"], r.get("held_fills")),
             TAG_LEGEND]
    lines += origin_summary(to_add, r["per_source"])
    lines += _validation_lines(r)
    lines.append("── paste this line (no tags) ──")
    lines.append(" ".join(to_add))          # COMPLETE list — the paste target
    if persisted:
        lines.append(f"⚠️ persisted ≥{PERSIST_WEEKS} wks: " + " ".join(persisted))
    lines.append("(paste the untagged line into MFR → Activate Assets)")
    return "\n".join(lines) + dark_footer()
