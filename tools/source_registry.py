"""Canonical membership registry — ONE lookup per signal source.

Every source is declared once with: a human name, natural-language aliases, a lookup()
returning its CURRENT member ticker set (uppercased), and a freshness query for
ingest-health. full_universe() is the de-duped union with per-source counts.

Reads only — no writes. Consumers: the MFR enrollment backlog (diffs the watchlist
against full_universe()) and the Telegram SOURCES command. A future SCREEN source=<tag>
will resolve via resolve(). Python owns all lookups; no LLM.

Membership definitions (confirmed 2026-07-05):
  etfpro  — hedgeye_etf_pro_ranges, latest week_of (unsided; parser computes bias but
            doesn't persist it to this table — see the etfpro-side follow-up)
  portsol — hedgeye_portfolio_solutions, latest snapshot_date
  ideas   — hedgeye_investing_ideas, latest snapshot_date (canonical over date_removed)
  keiths  — hedgeye_keiths_signals, latest signal_date
  sigstr  — ss_roster_current (delta-fed, always current)
  posmon  — ticker_tags where hedgeye_bucket_0629 IS NOT NULL (06-29 seed, no live feed)
  book    — book_positions latest snapshot, non-cash underlyings
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _members(sql: str) -> set:
    """Run a single-column ticker query and return an uppercased set. Reads only."""
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql)
        return {r[0].strip().upper() for r in cur.fetchall() if r and r[0]}


# ─────────────── per-source current-member queries ───────────────

def _etfpro() -> set:
    return _members("SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges "
                    "WHERE week_of = (SELECT max(week_of) FROM hedgeye_etf_pro_ranges)")

def _portsol() -> set:
    return _members("SELECT DISTINCT ticker FROM hedgeye_portfolio_solutions "
                    "WHERE snapshot_date = (SELECT max(snapshot_date) FROM hedgeye_portfolio_solutions)")

def _ideas() -> set:
    return _members("SELECT DISTINCT ticker FROM hedgeye_investing_ideas "
                    "WHERE snapshot_date = (SELECT max(snapshot_date) FROM hedgeye_investing_ideas)")

def _keiths() -> set:
    return _members("SELECT DISTINCT ticker FROM hedgeye_keiths_signals "
                    "WHERE signal_date = (SELECT max(signal_date) FROM hedgeye_keiths_signals)")

def sigstr_side(direction: str | None = None) -> set:
    """Signal Strength membership, SIDE-AWARE, from Keith's Signal Longs/Shorts.

    Delegates to tools.active_slice.source_breakdown(), which already selects
    `side` from hedgeye_keiths_signals at MAX(signal_date) behind the 14-day
    staleness gate. ONE source of truth — do not hand-write that query here or
    the two copies drift.

    direction: 'shorts' -> side=short, 'longs' -> side=long, None -> both.

    Replaces the old ss_roster_current read, which had NO side column: every
    membership row looked identical, so a `shorts` screen fell through to the
    bucket + trend_dir='BEARISH' gate and returned whatever happened to be
    bearish (JBS, a staple) instead of Keith's shorts. ss_roster_current is fed
    by the "Signal Strength Stocks" email — a different product from Keith's
    sided list, which arrives weekly from Hedgeye Financials Sector Pro.
    """
    try:
        from tools.active_slice import source_breakdown
        bd = source_breakdown() or {}
    except Exception as e:
        log.warning("sigstr: active_slice unavailable (%s)", e)
        return set()
    longs = {str(t).upper() for t in (bd.get("signal_strength_long") or [])}
    shorts = {str(t).upper() for t in (bd.get("signal_strength_short") or [])}
    d = (direction or "").lower()
    if d.startswith("short"):
        return shorts
    if d.startswith("long"):
        return longs
    return longs | shorts


def _sigstr() -> set:
    """BROAD Signal Strength — the ~68-name 'Signal Strength Stocks' roster.

    Deliberately NOT Keith's list. These are two different Hedgeye products:
    this one is the daily broad Add/Remove roster (side-less by nature), while
    Keith's Signal Longs/Shorts is a financials-sector product with an explicit
    side. Only the SHORTS direction routes to Keith's — see sigstr_side and the
    routing table in screener.run_screen_q.
    """
    return _members("SELECT ticker FROM ss_roster_current")


def _finsigstr() -> set:
    """Financials Signal Strength — both sides of Keith's list, for listings."""
    return sigstr_side(None)

def _posmon() -> set:
    return _members("SELECT ticker FROM ticker_tags WHERE hedgeye_bucket_0629 IS NOT NULL")

def _book() -> set:
    return _members("SELECT DISTINCT underlying FROM book_positions "
                    "WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions) "
                    "AND asset_class <> 'cash' AND COALESCE(quantity, 0) <> 0")

# BTC Quant coins carry the *USD suffix elsewhere (ticker_tags/mfr); equities/ETFs are
# as-is. Normalizes the bare tokens the CRYPTO QUANT parser stores.
BTCQ_NORM = {"BTC": "BTCUSD", "ETH": "ETHUSD", "SOL": "SOLUSD",
             "XRP": "XRPUSD", "AVAX": "AVAXUSD"}


def _btcquant() -> set:
    # Source of truth is hedgeye_crypto_quant (the existing CRYPTO QUANT parser, already
    # wired into email_parser + 184 rows). Members = names ever given a trend sentiment,
    # normalized to the canonical ticker (carry-forward; enroll-never-remove).
    raw = _members("SELECT DISTINCT asset FROM hedgeye_crypto_quant WHERE sentiment IS NOT NULL")
    return {BTCQ_NORM.get(t, t) for t in raw}


class Source:
    def __init__(self, tag, name, lookup, aliases, freshness_sql=None):
        self.tag = tag
        self.name = name
        self._lookup = lookup
        self.aliases = aliases
        self.freshness_sql = freshness_sql

    def members(self) -> set:
        try:
            return self._lookup()
        except Exception as e:
            log.warning("source_registry: %s lookup failed: %s", self.tag, e)
            return set()

    def latest(self):
        """Latest-update date for ingest health, or None if the source has no live feed."""
        if not self.freshness_sql:
            return None
        import db_pg
        try:
            with db_pg.get_conn() as c, c.cursor() as cur:
                cur.execute(self.freshness_sql)
                r = cur.fetchone()
                return r[0] if r else None
        except Exception as e:
            log.warning("source_registry: %s freshness failed: %s", self.tag, e)
            return None


REGISTRY = [
    Source("etfpro",  "ETF Pro",             _etfpro,
           ["etf pro", "etfpro", "etf_pro", "etf"],
           "SELECT max(parsed_at)::date FROM hedgeye_etf_pro_ranges"),
    Source("portsol", "Portfolio Solutions", _portsol,
           ["portfolio solutions", "portsol", "port sol", "solutions", "ps"],
           "SELECT max(snapshot_date) FROM hedgeye_portfolio_solutions"),
    Source("ideas",   "Investing Ideas",     _ideas,
           ["investing ideas", "ideas", "ii", "top 21", "best ideas"],
           "SELECT max(snapshot_date) FROM hedgeye_investing_ideas"),
    Source("keiths",  "Keith's Signals",     _keiths,
           ["keiths", "keith's signals", "keith", "signals"],
           "SELECT max(signal_date) FROM hedgeye_keiths_signals"),
    Source("sigstr",  "Signal Strength",     _sigstr,
           ["signal strength", "sigstr", "ss", "strength"],
           "SELECT max(added_on) FROM ss_roster_current"),
    # Distinct product from the broad roster above: Keith's Signal Longs/Shorts,
    # weekly from Hedgeye Financials Sector Pro, financials-only, explicitly
    # sided. Aliases are longer than "signal strength" and _source_phrase_list()
    # sorts by descending length, so these match first.
    Source("finsigstr", "Financials Signal Strength", _finsigstr,
           ["financials signal strength", "financial signal strength",
            "financials sigstr", "fin signal strength", "financials ss"],
           "SELECT max(signal_date) FROM hedgeye_keiths_signals"),
    Source("posmon",  "Position Monitor",    _posmon,
           ["position monitor", "posmon", "pm", "buckets"],
           None),   # static 06-29 seed, no live feed
    Source("book",    "My Book",             _book,
           ["book", "my book", "held", "holdings"],
           "SELECT max(snapshot_date) FROM book_positions"),
    Source("btcquant", "BTC Quant",          _btcquant,
           ["btc quant", "btcquant", "bitcoin quant", "btc"],   # NOT bare "crypto" (= sector)
           "SELECT max(signal_date) FROM hedgeye_crypto_quant WHERE sentiment IS NOT NULL"),
]
BY_TAG = {s.tag: s for s in REGISTRY}


def full_universe() -> dict:
    """De-duped union of every source's current members, with per-source detail.
    Reads only. Shape: {universe:set, per_source:{tag:[tickers]}, counts:{tag:n}, total:n}."""
    per_source, union = {}, set()
    for s in REGISTRY:
        m = s.members()
        per_source[s.tag] = m
        union |= m
    return {"universe": union,
            "per_source": {t: sorted(m) for t, m in per_source.items()},
            "counts": {t: len(m) for t, m in per_source.items()},
            "total": len(union)}


# ═══════════════ ENROLLMENT universe (all-time, deliberately wider) ═════════
# Measured 2026-08-01: the registry's universe is 509 names while MFR already has
# 623 activated, and 401 names Hedgeye has published data on sit outside it.
#
# Cause: every lookup above is CURRENT membership — MAX(week_of), MAX(
# snapshot_date), MAX(signal_date). Correct for "what is Keith long right now",
# wrong for "what should we hold range history on". ETF Pro alone has 166 names
# all-time and only 43 in the latest week, so 123 names Hedgeye published RANGES
# for are invisible; ~58 of those are already activated in MFR, meaning the data
# is arriving and nothing reads it.
#
# Operator decision 2026-08-01: "we want to enrol everything, we want a large
# source of data on our tradeable universe cuz we want to be able to go
# anywhere." That is also the repo's own enroll-never-remove doctrine — a name
# should not leave the universe because a weekly product stopped repeating it.
#
# DELIBERATELY SEPARATE from full_universe(). The current-membership lookups
# above drive SCREEN routing (sigstr_side, the shorts gate) and the SOURCES
# health display, both of which need "right now". Widening those would break
# them. This function is read by the enrollment backlog and nothing else.
#
# Prose-derived tables (the_call, macro_show) and the accumulated mention log
# (hedgeye_ticker_inventory) are NOT included: they carry parser scrape-through
# — the 2026-08-01 backlog junk (FROM, BEEN, LIST) accumulates exactly there.
# Mine those separately with _junk_sweep.py rather than enrolling them blind.
ALLTIME_SQL = {
    "etfpro":  "SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges "
               "WHERE range_low IS NOT NULL AND range_high IS NOT NULL",
    "portsol": "SELECT DISTINCT ticker FROM hedgeye_portfolio_solutions "
               "WHERE rank IS NOT NULL",
    "ideas":   "SELECT DISTINCT ticker FROM hedgeye_investing_ideas "
               "WHERE rank IS NOT NULL",
    "keiths":  "SELECT DISTINCT ticker FROM hedgeye_keiths_signals "
               "WHERE side IN ('long','short')",
    "riskrange": "SELECT DISTINCT ticker FROM hedgeye_risk_ranges "
                 "WHERE buy_trade IS NOT NULL AND sell_trade IS NOT NULL",
    "rta":       "SELECT DISTINCT ticker FROM hedgeye_rta "
                 "WHERE ticker IS NOT NULL AND ticker <> ''",
    "sigchange": "SELECT DISTINCT ticker FROM hedgeye_signal_changes "
                 "WHERE ticker IS NOT NULL AND ticker <> ''",
    "portactions": "SELECT DISTINCT ticker FROM hedgeye_portfolio_actions "
                   "WHERE ticker IS NOT NULL AND ticker <> ''",
    "iichanges": "SELECT DISTINCT ticker FROM hedgeye_ii_changes "
                 "WHERE ticker IS NOT NULL AND ticker <> ''",
    "hedgai":    "SELECT DISTINCT ticker FROM hedgeye_hedgai "
                 "WHERE ticker IS NOT NULL AND ticker <> ''",
    "momo":      "SELECT DISTINCT ticker FROM hedgeye_momo "
                 "WHERE ticker IS NOT NULL AND ticker <> ''",
    "retail":    "SELECT DISTINCT ticker FROM hedgeye_retail "
                 "WHERE ticker IS NOT NULL AND ticker <> ''",
}


def enrollment_universe() -> dict:
    """EVERY name Hedgeye has ever published data on, plus the live current-
    membership feeds (posmon / book / btcquant / sigstr). Read-only.

    Shape matches full_universe(): {universe, per_source, counts, total}. A
    source whose query fails is logged and skipped — never silently absent, and
    never fatal, because a missing table must not shrink the enrollment set."""
    per_source, union = {}, set()
    for tag, sql in ALLTIME_SQL.items():
        try:
            m = _members(sql)
        except Exception as e:
            log.warning("enrollment_universe: %s failed: %s", tag, e)
            m = set()
        per_source[tag] = m
        union |= m
    # current-membership sources that have no meaningful all-time form
    for tag in ("sigstr", "posmon", "book", "btcquant"):
        s = BY_TAG.get(tag)
        if not s:
            continue
        m = s.members()
        per_source[tag] = m
        union |= m
    return {"universe": union,
            "per_source": {t: sorted(m) for t, m in per_source.items()},
            "counts": {t: len(m) for t, m in per_source.items()},
            "total": len(union)}


def resolve(text) -> str | None:
    """Map a natural-language token to a source tag via tag/aliases (for SCREEN source=)."""
    t = (text or "").strip().lower()
    for s in REGISTRY:
        if t == s.tag or t in s.aliases:
            return s.tag
    return None


def handle_sources_command(text):
    """Telegram 'SOURCES' / '/sources' -> per-source member count + latest-update date
    (ingest health at a glance). Read-only. Returns None if not the command."""
    if not text or text.strip().upper() not in ("SOURCES", "/SOURCES"):
        return None
    fu = full_universe()
    lines = [f"📚 Sources — {fu['total']} distinct names across {len(REGISTRY)} feeds:"]
    for s in REGISTRY:
        n = fu["counts"].get(s.tag, 0)
        d = s.latest()
        when = f"updated {d}" if d else "static / no live feed"
        lines.append(f"  {s.name:<20} {n:>4}   {when}")
    return "\n".join(lines)
