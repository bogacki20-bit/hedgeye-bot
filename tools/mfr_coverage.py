"""tools/mfr_coverage.py — MFR COVERAGE: what the bot can range vs what it wants.

Three sets, kept apart because conflating them is how "the backlog is broken"
reports happen (2026-08-18 and 2026-08-24 both found the backlog CORRECT):

  WANTED   — tools.source_registry.full_universe()     (what we want ranges on)
  ENROLLED — mfr_client.list_watchlist()               (MFR account membership)
  SERVED   — distinct mfr_snapshots ticker, last 7d    (what actually delivers)

Backlog = WANTED - ENROLLED. Coverage = WANTED - SERVED. A name can be
ENROLLED and not SERVED (dark), or SERVED and not ENROLLED (alias forms like
BTC vs BTCUSD) — either mismatch produces "the same list keeps coming back".

`MFR COVERAGE` (Telegram) returns a .txt document. The ONLY write is one
mfr_backlog_snapshots row per day (078) — the nightly enrollment job writes
the same row, upsert, so the delta line "vs yesterday" is always answerable.

HELD AND DARK — held names with no MFR range — is deliberately computed from
the same primitive BOOK RP uses (tools.book_alerts._book_rows with
include_dark=True), so the two features can never disagree about it.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("mfr_coverage")

SENTINEL = "MFR COVERAGE"
SERVED_NAME_CAP = 30        # SERVED section prints names only under this
SERVED_WINDOW_DAYS = 7      # same window tools.enrollment uses


# ─────────────────────────── pure logic ───────────────────────────

def classify_universe(wanted, enrolled, served, parked) -> dict:
    """Pure. Split the WANTED universe into the four report sections.
    Parked wins over everything (deliberately excluded is not missing);
    then served, then enrolled-dark, then not-enrolled."""
    wanted, enrolled = set(wanted), set(enrolled)
    served, parked = set(served), set(parked)
    out = {"served": set(), "enrolled_dark": set(),
           "not_enrolled": set(), "parked": set()}
    for t in wanted:
        if t in parked:
            out["parked"].add(t)
        elif t in served:
            out["served"].add(t)
        elif t in enrolled:
            out["enrolled_dark"].add(t)
        else:
            out["not_enrolled"].add(t)
    return out


def is_dark_row(r) -> bool:
    """Pure. THE dark predicate, shared by BOOK RP and MFR COVERAGE so the two
    lists can never disagree. A held name is dark when the bot has no current
    range position for it — either it never made the source slice (the
    explicit "dark" flag from _book_rows) or its slice row carries no
    range_pos (no MFR band, no fresh Hedgeye band: rp_now is None)."""
    return bool(r.get("dark")) or r.get("rp_now") is None


def split_held(rows) -> tuple:
    """Pure. _book_rows(include_dark=True)-shaped dicts ->
    (dark_tickers, covered_tickers), each sorted. THE shared primitive:
    BOOK RP's dark list and this module's HELD AND DARK are both this."""
    dark = sorted({r["ticker"] for r in rows if is_dark_row(r)})
    covered = sorted({r["ticker"] for r in rows if not is_dark_row(r)})
    return dark, covered


def delta_line(today, backlog, prior) -> str:
    """Pure. The direct answer to "same list as yesterday?".
    prior is (prior_date, prior_backlog_set) or None on the first run."""
    if prior is None:
        return ("no prior backlog snapshot stored yet — deltas start "
                "tomorrow (078)")
    pd, prev = prior
    prev = set(prev)
    cur = set(backlog)
    cleared = sorted(prev - cur)
    new = sorted(cur - prev)
    line = f"vs {pd}: backlog {len(prev)} -> {len(cur)}"
    line += (", cleared: " + " ".join(cleared)) if cleared else ", cleared: none"
    line += (", new: " + " ".join(new)) if new else ", new: none"
    return line


def format_report(*, asof, universe, enrolled, served, parked,
                  backlog, backlog_note, held_rows, dark_days,
                  prior, divergences=None) -> str:
    """Pure formatter — everything IO-derived arrives as arguments so tests
    need no DB and no network.

    dark_days — {ticker: days_since_last_snapshot_or_None} for enrolled-dark
    names (None = never served a row)."""
    cls = classify_universe(universe, enrolled, served, parked)
    srv, edark = sorted(cls["served"]), sorted(cls["enrolled_dark"])
    nenr, prk = sorted(cls["not_enrolled"]), sorted(cls["parked"])
    held_dark, held_cov = split_held(held_rows)

    lines = [f"MFR COVERAGE — {asof}",
             f"universe {len(universe)} | enrolled {len(enrolled)} | "
             f"served {len(served)} (last {SERVED_WINDOW_DAYS}d) | "
             f"dark {len(edark)}",
             delta_line(asof, backlog, prior), ""]

    # Held names the bot cannot range: the highest-value block, so it leads.
    lines.append(f"HELD AND DARK ({len(held_dark)}) — held, no MFR range; "
                 f"the enrollment to-do:")
    lines.append("  " + " ".join(held_dark) if held_dark else "  none")
    lines.append(f"HELD AND COVERED ({len(held_cov)})")
    lines.append("")

    if not served:
        lines.append("SERVED: EMPTY — mfr_snapshots has NO rows in the last "
                     f"{SERVED_WINDOW_DAYS} days. That is a range-feed "
                     "failure (fan-out dead? DB write path broken?), not "
                     "755 names going dark at once. Fix the feed before "
                     "reading anything below.")
    elif len(srv) < SERVED_NAME_CAP:
        lines.append(f"SERVED ({len(srv)}): " + " ".join(srv))
    else:
        lines.append(f"SERVED ({len(srv)}) — enrolled and producing ranges "
                     f"(names elided at {SERVED_NAME_CAP}+)")

    lines.append("")
    lines.append(f"ENROLLED-DARK ({len(edark)}) — in the MFR account but no "
                 f"snapshot row in {SERVED_WINDOW_DAYS}d:")
    if edark:
        for t in edark:
            d = dark_days.get(t)
            lines.append(f"  {t:<8} last snapshot "
                         + (f"{d}d ago" if d is not None else "never"))
    else:
        lines.append("  none")

    lines.append("")
    lines.append(f"NOT-ENROLLED ({len(backlog)}) — the backlog, same list "
                 f"MFR BACKLOG prints:")
    lines.append("  " + " ".join(backlog) if backlog else "  none")
    if backlog_note:
        lines.append(f"  note: {backlog_note}")

    lines.append("")
    lines.append(f"PARKED ({len(prk)}) — deliberately excluded "
                 f"(KNOWN_UNCOVERABLE / PARKED_FOR_SOURCE), not missing:")
    lines.append("  " + " ".join(prk) if prk else "  none")

    lines.append("")
    # D4 (2026-08-26): today's published-vs-derived disagreements > 0.05 —
    # the check that would have caught the HYG 1.29-vs-0.89 defect on day one.
    lines.append(f"RP DIVERGENCE ({len(divergences or [])}) — published vs "
                 f"derived rp disagreeing by > 0.05 today:")
    if divergences:
        for t, pub, der, delta in divergences:
            lines.append(f"  {t:<8} published {pub:.2f}  derived {der:.2f}  "
                         f"delta {delta:.2f}")
    else:
        lines.append("  none recorded today")
    lines.append("")
    lines.append("HELD, by coverage:")
    for t in held_dark:
        tag = "ENROLLED-DARK" if t in set(enrolled) else "NOT-ENROLLED"
        lines.append(f"  {t:<8} HELD AND DARK ({tag})")
    for t in held_cov:
        lines.append(f"  {t:<8} SERVED")
    return "\n".join(lines)


# ─────────────────────────── snapshot store (078) ───────────────────────────

def record_snapshot(*, universe_count, enrolled_count, served_count,
                    backlog, snapshot_date=None) -> None:
    """Upsert today's mfr_backlog_snapshots row — the ONLY write this module
    performs. Best-effort: a failed write must never take the report down."""
    import db_pg
    sd = snapshot_date or date.today()
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mfr_backlog_snapshots
                     (snapshot_date, universe_count, enrolled_count,
                      served_count, backlog_count, backlog)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (snapshot_date) DO UPDATE SET
                     universe_count = EXCLUDED.universe_count,
                     enrolled_count = EXCLUDED.enrolled_count,
                     served_count   = EXCLUDED.served_count,
                     backlog_count  = EXCLUDED.backlog_count,
                     backlog        = EXCLUDED.backlog""",
                (sd, universe_count, enrolled_count, served_count,
                 len(backlog), " ".join(backlog)))
            conn.commit()
    except Exception as e:
        log.warning("mfr_backlog_snapshots write failed: %s", e)


def load_prior_snapshot(before=None):
    """(prior_date, prior_backlog_set) from the most recent stored day BEFORE
    `before` (default today), or None when there is none / the read fails."""
    import db_pg
    before = before or date.today()
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT snapshot_date, backlog FROM "
                        "mfr_backlog_snapshots WHERE snapshot_date < %s "
                        "ORDER BY snapshot_date DESC LIMIT 1", (before,))
            r = cur.fetchone()
        if not r:
            return None
        return r[0], set((r[1] or "").split())
    except Exception as e:
        log.warning("mfr_backlog_snapshots read failed: %s", e)
        return None


def record_daily_snapshot() -> str:
    """Compute the sets and upsert today's row. Called best-effort from the
    nightly enrollment job so the delta line has a yesterday even on days the
    operator never runs MFR COVERAGE. Returns a short status string."""
    from tools.enrollment import WatchlistUnavailable, compile_backlog
    try:
        r = compile_backlog()
    except WatchlistUnavailable as e:
        return f"skip:watchlist-unavailable ({e})"
    from tools.source_registry import full_universe
    served = _served_set()
    record_snapshot(universe_count=len(full_universe()["universe"]),
                    enrolled_count=r["active_count"],
                    served_count=len(served), backlog=r["to_add"])
    return f"stored:{len(r['to_add'])}"


# ─────────────────────────── IO assembly ───────────────────────────

def _served_set() -> set:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT upper(ticker) FROM mfr_snapshots "
                    "WHERE snapshot_date >= CURRENT_DATE - %s",
                    (SERVED_WINDOW_DAYS,))
        return {r[0] for r in cur.fetchall()}


def _dark_days(tickers) -> dict:
    """{ticker: days_since_last_snapshot_or_None} — all-time lookback, so a
    name that served once in July reads '34d ago', not 'never'."""
    if not tickers:
        return {}
    import db_pg
    out = {t: None for t in tickers}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT upper(ticker), CURRENT_DATE - max(snapshot_date) "
                    "FROM mfr_snapshots WHERE upper(ticker) = ANY(%s) "
                    "GROUP BY upper(ticker)", (sorted(tickers),))
        for t, d in cur.fetchall():
            out[t] = int(d)
    return out


def build_coverage_report() -> str:
    """Assemble the full report. One snapshot-row write (078); reads only
    otherwise. Raises nothing on its own — callers see stated failures in
    the body instead of a stack trace where possible."""
    from tools.enrollment import WatchlistUnavailable, compile_backlog
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE
    from tools.source_registry import full_universe
    import mfr_client

    universe = {t.upper() for t in full_universe()["universe"]}
    enrolled = {t.upper() for t in (mfr_client.list_watchlist() or [])}
    served = _served_set()
    parked = set(KNOWN_UNCOVERABLE) | set(PARKED_FOR_SOURCE)

    backlog, note = [], ""
    try:
        r = compile_backlog()
        backlog, note = r["to_add"], r.get("validation_note") or ""
    except WatchlistUnavailable as e:
        note = f"backlog not computed — {e}"

    from tools.book_alerts import _book_rows
    try:
        held_rows = _book_rows(include_dark=True)
    except Exception as e:
        log.warning("held-block assembly failed: %s", e)
        held_rows = []

    cls = classify_universe(universe, enrolled, served, parked)
    dark_days = _dark_days(cls["enrolled_dark"])

    try:
        from tools.rp_resolve import todays_divergences
        divergences = todays_divergences()
    except Exception as e:
        log.warning("rp divergence section unavailable: %s", e)
        divergences = []

    today = date.today()
    prior = load_prior_snapshot(before=today)
    body = format_report(asof=today, universe=universe, enrolled=enrolled,
                         served=served, parked=parked, backlog=backlog,
                         backlog_note=note, held_rows=held_rows,
                         dark_days=dark_days, prior=prior,
                         divergences=divergences)
    record_snapshot(universe_count=len(universe),
                    enrolled_count=len(enrolled),
                    served_count=len(served), backlog=backlog)
    return body


# ─────────────────────────── operator command ───────────────────────────

def handle_coverage_command(text: str):
    """Telegram hook — owns 'MFR COVERAGE'. None to decline. Returns a
    document dict ({document_name, document_text, caption}) because the body
    exceeds Telegram's 4096-char inline limit."""
    if not text:
        return None
    up = " ".join(text.strip().upper().split()).rstrip(".!?")
    if up != SENTINEL:
        return None
    try:
        body = build_coverage_report()
    except Exception as e:
        log.exception("MFR COVERAGE failed")
        return f"MFR COVERAGE error: {e}"
    return {"document_name": f"mfr_coverage_{date.today()}.txt",
            "document_text": body,
            "caption": "MFR coverage — wanted / enrolled / served"}
