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

def _sigstr() -> set:
    return _members("SELECT ticker FROM ss_roster_current")

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
