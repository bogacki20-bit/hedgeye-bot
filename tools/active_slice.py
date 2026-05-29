"""Active universe + polling universe resolvers.

Two universes coexist:

1. `active_universe(side)` — the original Quad-filtered slice
   (intersection of the monthly and quarterly Quad in
   `config/mfr_quad_map.yaml`). Still used for prioritization, alignment
   labels and back-compat with any code that imports it.

2. `polling_universe()` — the union the scanner actually iterates over.
   Always-on superset that captures every ticker Keith McCullough is
   currently surfacing through any Hedgeye product, regardless of Quad:
       • Active Quad slice           (longs ∪ shorts from `active_universe`)
       • ETF Pro long ranks          (`hedgeye_etf_pro_ranges`, bias=long)
       • ETF Pro short ranks         (`hedgeye_etf_pro_ranges`, bias=short)
       • Signal Strength             (`hedgeye_signal_strength`)
       • Portfolio Solutions         (`hedgeye_portfolio_solutions`)
       • Risk Range published        (`hedgeye_risk_ranges`)
       • Operator overrides          (`config/operator_watchlist.yaml`)

   The rollback on 2026-05-24 collapsed the polled universe to just the
   Quad slice (~39 tickers), which left most of ETF Pro / SS / PS out of
   the loop. This restores the full union (~120-200 tickers) so every
   product Keith publishes against gets price-zone monitoring.

3. `source_flags_for(ticker)` — list of source labels for a ticker
   (`etf_pro_long`, `etf_pro_short`, `signal_strength`,
   `portfolio_solutions`, `risk_range`, `operator`, `quad_aligned`).
   Surfaced into the Haiku notifier prompt so the alert body can say
   "[ETF Pro Short] WATCH XLF — ...".

Both DB-backed lookups are wrapped in a 5-minute in-memory TTL cache so
a scanner cycle doesn't re-query Postgres for every ticker.

CLI:
    python -m tools.active_slice long          # quad slice, long side
    python -m tools.active_slice short
    python -m tools.active_slice both
    python -m tools.active_slice polling        # full polling universe
    python -m tools.active_slice polling --breakdown
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Literal

import yaml

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
_MAP_PATH = _REPO / "config" / "mfr_quad_map.yaml"
_OPERATOR_WATCHLIST_PATH = _REPO / "config" / "operator_watchlist.yaml"

Side = Literal["long", "short"]

# Per-source staleness thresholds. 2026-05-26 v2 (post-clarification):
# the polling universe is the STRICT UNION of CURRENT publications only —
# tickers no longer in Keith's most-recent publication drop out of the
# polling stream on the same cycle. Operator's words: "things that are
# taken off signal strength should be removed from alert", "the bot's
# polling universe should mirror Keith's CURRENT opinions, not accumulate
# stale ones".
#
# A publication counts as CURRENT when its latest snapshot date is
# within these thresholds. If older, the bucket gets the `stale_*`
# flag — kept in source_breakdown for diagnostics but EXCLUDED from
# polling_universe (see _POLLING_INCLUDED_KEYS).
#
# Thresholds:
#   etf_pro              -> 7 days (Hedgeye publishes ETF Pro Mon, +1wk slack)
#   signal_strength      -> 3 days (daily; 3d covers a long weekend)
#   portfolio_solutions  -> 3 days (daily)
#   investing_ideas      -> 3 days (daily)
#   risk_range           -> 3 days (daily)
SOURCE_STALENESS_DAYS = {
    "etf_pro":             7,
    "signal_strength":     3,
    "portfolio_solutions": 3,
    "investing_ideas":     3,
    "risk_range":          3,
}

# TTL for the polling-universe cache. 5 min keeps the scanner from
# hammering Postgres mid-cycle but still picks up new tickers within one
# scan interval after a parser writes them.
_CACHE_TTL_SECONDS = 300

_cache: dict = {"data": None, "expires_at": 0.0}
_cache_lock = threading.Lock()

# Buckets that contribute to polling_universe (strict-current membership,
# operator clarification 2026-05-26 v2). `stale_*` buckets exist in
# source_breakdown for diagnostics only — they do NOT add tickers to the
# polling stream. Result: when Keith drops a ticker from a product, it
# falls out of the bot's polling on the same cycle the parser runs
# (modulo the 5-min TTL cache).
_POLLING_INCLUDED_KEYS = frozenset({
    "quad_long", "quad_short",          # reference (no staleness concept)
    "etf_pro_long", "etf_pro_short",    # latest weekly publication, fresh
    "signal_strength",                  # latest daily snapshot, fresh
    "portfolio_solutions",              # latest daily snapshot, fresh
    "investing_ideas",                  # latest daily snapshot, fresh
    "risk_range",                       # last 3 days
    "operator",                         # operator override yaml
})


def _load_map() -> dict:
    if not _MAP_PATH.exists():
        log.warning("mfr_quad_map.yaml missing at %s — empty universe", _MAP_PATH)
        return {"tickers": {}}
    with open(_MAP_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"tickers": {}}


def _quad_key(quad: str) -> str:
    """'Quad 2' -> 'quad_2'. Tolerates 'quad2', 'q2', '2'."""
    s = str(quad).strip().lower().replace("quad", "").replace("q", "").strip()
    if s in ("1", "2", "3", "4"):
        return f"quad_{s}"
    raise ValueError(f"unrecognized Quad label: {quad!r}")


def active_universe(side: Side,
                    monthly_quad: str | None = None,
                    quarterly_quad: str | None = None) -> list[str]:
    """Tickers tagged `side` for both the active monthly AND quarterly Quad.

    Args:
        side: 'long' or 'short'.
        monthly_quad / quarterly_quad: if None, pulled from tools.doctrine
            (which respects the CURRENT_*_QUAD_OVERRIDE env vars).
    Returns:
        Sorted list of tickers. Empty list when no map or no overlap.
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if monthly_quad is None or quarterly_quad is None:
        from tools.doctrine import current_monthly_quad, current_quarterly_quad
        monthly_quad   = monthly_quad   or current_monthly_quad()
        quarterly_quad = quarterly_quad or current_quarterly_quad()

    mq = _quad_key(monthly_quad)
    qq = _quad_key(quarterly_quad)

    data = _load_map()
    out: list[str] = []
    for ticker, sides in (data.get("tickers") or {}).items():
        if not isinstance(sides, dict):
            continue
        if sides.get(mq) == side and sides.get(qq) == side:
            out.append(ticker.upper())
    return sorted(out)


def both_sides(monthly_quad: str | None = None,
               quarterly_quad: str | None = None) -> dict[str, list[str]]:
    """Convenience: {'long': [...], 'short': [...]} for the active Quads."""
    return {
        "long":  active_universe("long",  monthly_quad, quarterly_quad),
        "short": active_universe("short", monthly_quad, quarterly_quad),
    }


# ─────────────────────────── Polling universe ───────────────────────────

def _load_operator_watchlist() -> list[str]:
    """Read config/operator_watchlist.yaml — empty list if missing/malformed.

    Operator-managed overrides. Anything in here gets polled regardless of
    Quad alignment or Hedgeye-product membership."""
    if not _OPERATOR_WATCHLIST_PATH.exists():
        return []
    try:
        with open(_OPERATOR_WATCHLIST_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        raw = data.get("tickers") or []
        if not isinstance(raw, list):
            log.warning("operator_watchlist: 'tickers' not a list, ignoring")
            return []
        out: list[str] = []
        for t in raw:
            if isinstance(t, str) and t.strip():
                out.append(t.strip().upper())
        return sorted(set(out))
    except Exception as e:
        log.warning("operator_watchlist load failed: %s", e)
        return []


def _pg_connect():
    """Lazy Postgres connect using the same env precedence as the rest of
    the bot. Returns None if either psycopg2 or the connection string is
    missing — callers degrade to empty lists rather than crashing the
    scanner. Polling-universe stays useful (Quad slice + operator) even
    when the DB is unreachable."""
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        log.warning("active_slice: DATABASE_*_URL not set; DB-backed sources will be empty")
        return None
    try:
        import psycopg2
    except ImportError:
        log.warning("active_slice: psycopg2 not installed; DB-backed sources will be empty")
        return None
    try:
        return psycopg2.connect(url)
    except Exception as e:
        log.warning("active_slice: psycopg2.connect failed (%s); DB-backed sources empty", e)
        return None


def _normalize_ticker_safely(t):
    """Apply tools.ticker_aliases.normalize_ticker when available; bare
    upper-strip fallback otherwise. Used in a hot loop, so we cache the
    import."""
    try:
        try:
            from tools.ticker_aliases import normalize_ticker
        except ImportError:
            from ticker_aliases import normalize_ticker  # type: ignore
        return normalize_ticker(t) or ""
    except Exception:
        return (t or "").strip().upper()


def _dedup_sort(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in lst:
        n = _normalize_ticker_safely(t)
        if n and n.strip() and n not in seen:
            seen.add(n)
            out.append(n)
    return sorted(out)


def _fetch_db_sources() -> dict[str, list[str]]:
    """Pull source-flag buckets, latest-publication-only.

    Returns keys (sorted, deduped ticker lists):
      etf_pro_long, etf_pro_short          ← latest weekly publication, fresh
      stale_etf_pro                        ← latest weekly publication, BUT stale
                                             (>SOURCE_STALENESS_DAYS['etf_pro']
                                              days old; NOT in polling_universe)
      signal_strength                      ← latest snapshot, fresh
      stale_signal_strength                ← latest snapshot, but stale
                                             (NOT in polling_universe)
      portfolio_solutions                  ← latest snapshot, fresh
      stale_portfolio_solutions            ← latest snapshot, but stale
                                             (NOT in polling_universe)
      investing_ideas                      ← latest snapshot, fresh
      stale_investing_ideas                ← latest snapshot, but stale
                                             (NOT in polling_universe)
      risk_range                           ← any ticker with an RR in the
                                             staleness window (RR is daily +
                                             always-on; no "snapshot" concept)
      stale_risk_range                     ← intentionally always empty under
                                             strict-current policy; kept as a
                                             stable key so source_flags_for
                                             callers don't have to None-check

    No `follow_up` bucket — operator clarification 2026-05-26 v2 dropped
    the 21-day catch-all. Polling universe = strict union of current
    publications + Quad + operator.

    Empty everywhere on connect failure (returns the empty dict shape so
    callers don't have to None-check)."""
    out: dict[str, list[str]] = {
        "etf_pro_long":              [],
        "etf_pro_short":             [],
        "stale_etf_pro":             [],
        "signal_strength":           [],
        "stale_signal_strength":     [],
        "portfolio_solutions":       [],
        "stale_portfolio_solutions": [],
        "investing_ideas":           [],
        "stale_investing_ideas":     [],
        "risk_range":                [],
        "stale_risk_range":          [],
    }
    conn = _pg_connect()
    if conn is None:
        return out

    try:
        with conn:
            with conn.cursor() as cur:
                # ─── ETF Pro: latest weekly publication only ────────────
                cur.execute("""
                    SELECT MAX(week_of), CURRENT_DATE - MAX(week_of)
                      FROM hedgeye_etf_pro_ranges
                """)
                row = cur.fetchone()
                latest_week, age_days = (row or (None, None))
                if latest_week is not None:
                    cur.execute("""
                        SELECT ticker, COALESCE(description, '')
                          FROM hedgeye_etf_pro_ranges
                         WHERE week_of = %s
                    """, (latest_week,))
                    longs:  list[str] = []
                    shorts: list[str] = []
                    for t, desc in cur.fetchall():
                        if not t:
                            continue
                        tk = t.strip().upper()
                        if desc.upper().startswith("SHORT"):
                            shorts.append(tk)
                        else:
                            # description prefix LONG | …  OR older un-tagged
                            # rows (treat as long — ETF Pro is overwhelmingly
                            # a long product).
                            longs.append(tk)
                    is_stale = (age_days is not None
                                and age_days > SOURCE_STALENESS_DAYS["etf_pro"])
                    if is_stale:
                        # Stale → diagnostic bucket only, NOT in polling.
                        out["stale_etf_pro"] = _dedup_sort(longs + shorts)
                        log.info("active_slice: ETF Pro latest week_of=%s is "
                                 "%dd old (>%dd threshold); marked stale, "
                                 "EXCLUDED from polling_universe",
                                 latest_week, int(age_days),
                                 SOURCE_STALENESS_DAYS["etf_pro"])
                    else:
                        out["etf_pro_long"]  = _dedup_sort(longs)
                        out["etf_pro_short"] = _dedup_sort(shorts)

                # ─── Helper for daily-snapshot products ──────────────────
                def _latest_snapshot(table: str, date_col: str,
                                     fresh_key: str, stale_key: str,
                                     stale_days: int) -> None:
                    cur.execute(f"""
                        SELECT MAX({date_col}),
                               CURRENT_DATE - MAX({date_col})
                          FROM {table}
                    """)
                    row = cur.fetchone()
                    latest, age = (row or (None, None))
                    if latest is None:
                        return
                    cur.execute(
                        f"SELECT DISTINCT ticker FROM {table} WHERE {date_col} = %s",
                        (latest,),
                    )
                    tickers = _dedup_sort([r[0] for r in cur.fetchall() if r[0]])
                    is_stale = (age is not None and age > stale_days)
                    if is_stale:
                        out[stale_key] = tickers
                        log.info("active_slice: %s latest %s=%s is %dd old "
                                 "(>%dd threshold); marked stale, EXCLUDED "
                                 "from polling_universe",
                                 table, date_col, latest, int(age), stale_days)
                    else:
                        out[fresh_key] = tickers

                _latest_snapshot("hedgeye_signal_strength",     "snapshot_date",
                                 "signal_strength", "stale_signal_strength",
                                 SOURCE_STALENESS_DAYS["signal_strength"])
                _latest_snapshot("hedgeye_portfolio_solutions", "snapshot_date",
                                 "portfolio_solutions", "stale_portfolio_solutions",
                                 SOURCE_STALENESS_DAYS["portfolio_solutions"])
                # II may not be migrated on every env — guard with probe.
                try:
                    cur.execute("SELECT 1 FROM hedgeye_investing_ideas LIMIT 1")
                    _latest_snapshot("hedgeye_investing_ideas", "snapshot_date",
                                     "investing_ideas", "stale_investing_ideas",
                                     SOURCE_STALENESS_DAYS["investing_ideas"])
                except Exception:
                    pass

                # ─── Risk Range: last N days (no "publication" — daily) ─
                stale_rr = SOURCE_STALENESS_DAYS["risk_range"]
                cur.execute(
                    "SELECT DISTINCT ticker FROM hedgeye_risk_ranges "
                    "WHERE signal_date >= CURRENT_DATE - interval %s",
                    (f"{stale_rr} days",),
                )
                out["risk_range"] = _dedup_sort([r[0] for r in cur.fetchall()
                                                  if r[0]])
                # stale_risk_range stays [] under strict-current policy.

    except Exception as e:
        log.warning("active_slice: DB universe fetch failed (%s); "
                    "returning partial result", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return out


def _compute_breakdown() -> dict[str, list[str]]:
    """Compute the full {source: [tickers]} breakdown for the polling
    universe. Caller must not mutate the returned dict — it lives in the
    TTL cache."""
    breakdown: dict[str, list[str]] = {}

    # Quad-aligned slice (intersection of monthly + quarterly Quad).
    # 2026-05-29 retune (CASY false-COVER bug): when env vars are
    # missing, tools.doctrine silently defaults to Quad 1 — under which
    # CASY (and many others) is tagged quad_short, which then drove
    # side=short and gated to COVER on a non-short ticker. The right
    # behavior when the operator hasn't told us the active regime is:
    # don't pretend to know. Skip Quad bucket population entirely —
    # tickers only in fresh Hedgeye products (ETF Pro, SS, PS, II, RR)
    # or the operator override list still get polled; tickers known
    # only via the static Quad map go silent until the operator sets
    # CURRENT_*_QUAD_OVERRIDE.
    have_quad_env = bool(os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE")
                          and os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE"))
    if have_quad_env:
        try:
            longs  = active_universe("long")
            shorts = active_universe("short")
        except Exception as e:
            log.warning("active_slice: Quad slice unavailable (%s); "
                        "skipping quad bucket population", e)
            longs, shorts = [], []
    else:
        log.warning("active_slice: CURRENT_MONTHLY_QUAD_OVERRIDE / "
                    "CURRENT_QUARTERLY_QUAD_OVERRIDE not set — skipping "
                    "quad_long/quad_short tagging. Set both env vars to "
                    "include Quad-categorized tickers in polling.")
        longs, shorts = [], []
    breakdown["quad_long"]  = longs
    breakdown["quad_short"] = shorts

    # DB-backed product universes (latest publication + staleness gates +
    # follow_up bucket for the 21-day inclusion window).
    db = _fetch_db_sources()
    breakdown.update(db)

    # Operator overrides.
    breakdown["operator"] = _load_operator_watchlist()

    return breakdown


def source_breakdown() -> dict[str, list[str]]:
    """Cached {source_label: [tickers]} mapping. Refreshes every
    _CACHE_TTL_SECONDS.

    Strict-current policy (2026-05-26 v2): polling_universe is the union
    of the FRESH buckets + Quad + operator. `stale_*` buckets are
    visible here for diagnostics ("what publication just went stale?")
    but they do NOT contribute to polling_universe — when Keith removes
    a ticker from a product, it drops out of the polling stream on the
    next cycle. The delta-alert system explicitly tells the operator
    when this happens.

    Buckets contributing to polling_universe:
        quad_long, quad_short          — from config/mfr_quad_map.yaml
        etf_pro_long, etf_pro_short    — latest weekly publication (fresh)
        signal_strength                — latest snapshot (fresh)
        portfolio_solutions            — latest snapshot (fresh)
        investing_ideas                — latest snapshot (fresh)
        risk_range                     — last 3 days
        operator                       — config/operator_watchlist.yaml

    Diagnostic-only buckets (NOT in polling_universe):
        stale_etf_pro                  — latest publication is >7 days old
        stale_signal_strength          — latest snapshot is >3 days old
        stale_portfolio_solutions      — latest snapshot is >3 days old
        stale_investing_ideas          — latest snapshot is >3 days old
        stale_risk_range               — kept for shape compatibility; empty
    """
    now = time.monotonic()
    with _cache_lock:
        if _cache["data"] is not None and now < _cache["expires_at"]:
            return _cache["data"]
        data = _compute_breakdown()
        _cache["data"] = data
        _cache["expires_at"] = now + _CACHE_TTL_SECONDS
        return data


def polling_universe() -> list[str]:
    """Sorted union of CURRENT (fresh) source buckets + Quad + operator.

    Strict-current membership (operator clarification 2026-05-26 v2):
    when Keith removes a ticker from a publication, it drops out of the
    polling stream on the next refresh cycle. `stale_*` buckets are
    visible in source_breakdown() for diagnostics but do NOT contribute
    here.

    The delta-alert system (tools.event_driven_alerts) tells the operator
    explicitly when a ticker is removed from a publication — that's the
    audit trail for why a ticker dropped from the polling stream.
    """
    bd = source_breakdown()
    uni: set[str] = set()
    for key in _POLLING_INCLUDED_KEYS:
        uni.update(bd.get(key, []))
    return sorted(uni)


def source_flags_for(ticker: str) -> list[str]:
    """Fresh-only source labels a single ticker belongs to.

    Returns sorted list of buckets the ticker is in, EXCLUDING `stale_*`
    buckets. Stale flags are diagnostic-only (visible in
    `source_breakdown()` and via `stale_flags_for()`) but never drive
    the [SourceLabel] cascade or `_ticker_side` resolution.

    2026-05-29 policy retune (operator: CASY false-COVER bug). Before:
    stale_investing_ideas was emitted alongside other flags, then the
    label cascade rendered "[Investing Ideas (stale)]" and the
    side-resolution heuristic was vulnerable to anything ending in
    `_short`. After: stale buckets stay in source_breakdown for the
    delta-alert/diagnostic surface but never leak into the action-verb
    pipeline.
    """
    if not ticker:
        return []
    tk = ticker.strip().upper()
    bd = source_breakdown()
    flags: list[str] = []
    for key, lst in bd.items():
        if key.startswith("stale_"):
            continue   # diagnostic-only; see stale_flags_for()
        if tk in lst:
            flags.append(key)
    if "quad_long" in flags or "quad_short" in flags:
        flags.append("quad_aligned")
    return sorted(set(flags))


def stale_flags_for(ticker: str) -> list[str]:
    """Stale-only buckets a ticker appears in.

    Companion to `source_flags_for` that exposes the staleness diagnostic
    without polluting the action-verb pipeline. Use this in audit or
    operator-visibility surfaces ("which tickers are surviving on stale
    data") — NOT to drive side resolution or alert labels.
    """
    if not ticker:
        return []
    tk = ticker.strip().upper()
    bd = source_breakdown()
    return sorted({k for k, lst in bd.items()
                   if k.startswith("stale_") and tk in lst})


def invalidate_cache() -> None:
    """Drop the cached breakdown. Useful in tests / after a manual parser
    backfill when you want the next scan to pick up new tickers without
    waiting for the TTL to elapse."""
    with _cache_lock:
        _cache["data"] = None
        _cache["expires_at"] = 0.0


# ─────────────────────────── CLI ───────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.active_slice")
    ap.add_argument("side",
                    choices=("long", "short", "both", "polling"),
                    help="Which universe to print. 'long'/'short'/'both' = "
                         "Quad-filtered slice (back-compat). 'polling' = "
                         "full polling universe (Quad + ETF Pro + SS + PS + "
                         "RR + operator overrides).")
    ap.add_argument("--monthly", default=None,
                    help='Override monthly Quad (e.g. "Quad 2"). Defaults to env.')
    ap.add_argument("--quarterly", default=None,
                    help='Override quarterly Quad (e.g. "Quad 3"). Defaults to env.')
    ap.add_argument("--count", action="store_true",
                    help="Print counts only, not ticker lists")
    ap.add_argument("--breakdown", action="store_true",
                    help="(polling only) Print per-source counts and "
                         "tickers, not just the union.")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.side == "polling":
        bd = source_breakdown()
        uni = polling_universe()
        if a.breakdown:
            for key in sorted(bd):
                lst = bd[key]
                print(f"=== {key} ({len(lst)}) ===")
                if not a.count:
                    print(", ".join(lst) or "(none)")
            print(f"=== UNION ({len(uni)}) ===")
            if not a.count:
                print(", ".join(uni) or "(none)")
        else:
            if a.count:
                print(len(uni))
            else:
                print("\n".join(uni))
        return 0

    if a.side == "both":
        sides = both_sides(a.monthly, a.quarterly)
        for s in ("long", "short"):
            print(f"=== {s.upper()} ({len(sides[s])}) ===")
            if not a.count:
                print(", ".join(sides[s]) or "(none)")
    else:
        uni = active_universe(a.side, a.monthly, a.quarterly)
        if a.count:
            print(len(uni))
        else:
            print("\n".join(uni))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
