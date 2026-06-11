# spotgamma stripped from live path 2026-05-24; historical snapshots still ingested for ML
"""Proactive idea scanner — the bot's own trade-idea generator.

Where the email parser only acts when Hedgeye sends an email, this scanner
runs on a market-hours loop, refreshes the multi-source context for each
ticker in the inventory, and calls decision_engine.decide() to see whether
a setup has lined up since the last scan.

Pipeline per ticker:
  1. unified_refresh.refresh_all_for_tickers([ticker])  — MFR/SG/Yahoo lockstep
  2. decision_engine.decide(ticker, signal_origin='proactive_scan')
  3. If (conviction, action) is actionable AND not deduped against a recent
     trade_recommendation → persist + send Telegram alert

What "actionable" means:
  - conviction in {'Best Idea', 'Adding'}
  - action in {'BUY', 'ADD', 'TRIM', 'SELL'}
  - bps populated (or close-direction trim/sell)
Everything else is logged as 'observed' but does not page the user.

Dedup window (default 4 hours): if the same (ticker, action, conviction)
fired a trade_recommendations row in the last N hours, suppress.

CLI:
    py proactive_scanner.py                    # scan all monitored tickers
    py proactive_scanner.py --max-tickers 10   # limit to first N (newest)
    py proactive_scanner.py --tickers OIH XOP  # explicit list
    py proactive_scanner.py --dry-run          # decide but don't notify/persist
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger(__name__)


ACTIONABLE_CONVICTIONS = {"Best Idea", "Adding"}
# 2026-05-26 v3 — trend+momentum gate added HOLD / WATCH / AVOID as
# informational alerts that fire when the bot is at an edge but the
# gate disagrees with a clean BUY/SELL. 2026-05-28 — side-aware verbs
# add SHORT (initiate/add to short) and COVER (close short). The
# conviction gate is what distinguishes "edge alert" from "mid_range
# silent" — decide_notifier sets conviction=Monitor for mid_range/
# unknown short-circuits and Adding for everything that reaches an
# edge/breakout zone.
ACTIONABLE_ACTIONS     = {"BUY", "SELL", "SHORT", "COVER",
                          "HOLD", "WATCH", "AVOID",
                          "ADD", "TRIM"}
DEFAULT_DEDUP_HOURS    = 4


# ─────────────────────────── Watchlist resolution ───────────────────────────

def _active_quad(use_monthly: bool = False) -> str:
    """Active Quad for universe/alignment — quarterly (strategic) by
    default, monthly (tactical) when --use-monthly-quad is set.

    Lets QuadUnsetError propagate (2026-06-10): the old `default Quad 1`
    branch was the silent failure mode the surgery is meant to kill.
    Callers that can't tolerate it must preflight via scan().
    """
    from tools.doctrine import current_monthly_quad, current_quarterly_quad
    return current_monthly_quad() if use_monthly else current_quarterly_quad()


def _resolve_watchlist(explicit: Optional[list[str]],
                      max_tickers: Optional[int],
                      priority: str = "all",
                      quad_filtered: bool = False,
                      use_monthly_quad: bool = False,
                      source: str = "monitored",
                      lookback_days: int = 7) -> list[str]:
    """Return the deduped list of tickers this scan should evaluate.

    Resolution order:
      explicit > source=polling_universe (full union — production default
      2026-05-25, see tools.active_slice.polling_universe) >
      source=active_slice (monthly ∩ quarterly Quad from
      config/mfr_quad_map.yaml — the previous default, still selectable for
      Quad-only runs) > source=hedgeye_active (Hedgeye product feed union)
      > source=quad / --quad-filtered (doctrine Quad universe) >
      tools.list_monitored_tickers (with --priority) > ticker_inventory view.

    `source`:
      'polling_universe' — superset the scanner should iterate over. Union
        of: Quad slice (long+short), ETF Pro long+short, Signal Strength,
        Portfolio Solutions, Risk Range, operator overrides. This is the
        "every Hedgeye product Keith publishes against gets monitored"
        universe — ~120-200 tickers under normal operating conditions.
        Each ticker's source membership flows through to decision_engine
        via tools.active_slice.source_flags_for().
      'active_slice' — Quad-filtered subset (intersection of monthly and
        quarterly Quads). Useful for Quad-only test runs; the polling
        universe already includes it.
      'hedgeye_active' — tickers Keith is ACTIVELY surfacing through Hedgeye
        products (RR+SS+PS+ETF Pro+II) in the last `lookback_days`.
      'quad' — Hedgeye favored longs+shorts for the active Quad (the slide-
        derived framework reference; also reached via legacy --quad-filtered).
      'monitored' — the legacy ticker_inventory view, sliced by `priority`.

    `priority` ('high'/'tail'/'all') only applies to source='monitored'.

    When `quad_filtered` and no explicit list is given, the universe is the
    Hedgeye favored longs+shorts for the active Quad (monthly if
    `use_monthly_quad`, else quarterly).
    """
    if explicit:
        return sorted({t.upper().strip() for t in explicit if t and t.strip()})

    if source == "polling_universe":
        try:
            from tools.active_slice import polling_universe, source_breakdown
            uni = polling_universe()
            if uni:
                bd = source_breakdown()
                # Per-source counts so daily logs make it obvious whether a
                # parser stopped writing (e.g. SS=0 means the Monday SS
                # email never parsed). The union count is what the
                # operator cares about; the breakdown is the diagnostic.
                summary = ", ".join(f"{k}={len(v)}" for k, v in sorted(bd.items()))
                log.info("scanner: polling_universe = %d tickers (%s)",
                         len(uni), summary)
                if max_tickers and len(uni) > max_tickers:
                    uni = uni[:max_tickers]
                return uni
            log.warning("scanner: polling_universe empty — every source "
                        "returned 0 tickers. Check DB connectivity and "
                        "parser freshness; falling back to active_slice.")
        except Exception as e:
            log.warning("scanner: polling_universe resolver failed (%s); "
                        "falling back to active_slice", e)
        # Fall through to active_slice — keeps the scanner running on the
        # Quad-only slice rather than scanning nothing when the DB blips.
        source = "active_slice"

    if source == "active_slice":
        try:
            from tools.active_slice import active_universe
            longs  = active_universe("long")
            shorts = active_universe("short")
            uni = sorted(set(longs) | set(shorts))
            if uni:
                log.info("scanner: active_slice universe = %d tickers "
                         "(%d long, %d short)", len(uni), len(longs), len(shorts))
                if max_tickers and len(uni) > max_tickers:
                    uni = uni[:max_tickers]
                return uni
            log.warning("scanner: active_slice empty — check Quad env vars "
                        "and config/mfr_quad_map.yaml; falling back")
        except Exception as e:
            log.warning("scanner: active_slice resolver failed (%s); "
                        "falling back", e)

    if source == "hedgeye_active":
        try:
            from tools import list_monitored_tickers as L
            uni = L.fetch_hedgeye_active(lookback_days)
            if uni:
                log.info("scanner: hedgeye-active universe = %d tickers "
                         "(lookback=%dd)", len(uni), lookback_days)
                if max_tickers and len(uni) > max_tickers:
                    uni = uni[:max_tickers]
                return uni
            log.warning("scanner: hedgeye-active universe empty; falling back")
        except Exception as e:
            log.warning("scanner: hedgeye-active resolver failed (%s); "
                        "falling back", e)

    if quad_filtered or source == "quad":
        try:
            from tools.doctrine import universe_for_quad
            q = _active_quad(use_monthly_quad)
            uni = universe_for_quad(q, "longs") + universe_for_quad(q, "shorts")
            uni = sorted(set(uni))
            if uni:
                log.info("scanner: quad-filtered universe = %d tickers (%s)",
                         len(uni), q)
                if max_tickers and len(uni) > max_tickers:
                    uni = uni[:max_tickers]
                return uni
            log.warning("scanner: quad universe empty; falling back to inventory")
        except Exception as e:
            log.warning("scanner: quad-filter failed (%s); falling back", e)

    tickers: list[str] = []
    try:
        from tools import list_monitored_tickers as L
        if priority == "high":
            tickers = L.fetch_high()
        elif priority == "tail":
            tickers = L.fetch_tail()
        else:
            tickers = L.fetch_all()
    except Exception as e:
        log.warning("scanner: list_monitored_tickers unavailable, falling back: %s", e)
        try:
            import ticker_inventory
            tickers = ticker_inventory.monitored_tickers() or []
        except Exception as e2:
            log.warning("scanner: ticker_inventory also unavailable: %s", e2)
            tickers = []

    if max_tickers and len(tickers) > max_tickers:
        tickers = tickers[:max_tickers]
    return tickers


# ─────────────────────────── Dedup against recent recs ───────────────────────────

def _was_recently_alerted(ticker: str, action: str, conviction: str,
                         within_hours: int = DEFAULT_DEDUP_HOURS) -> bool:
    """True if trade_recommendations has a same-ticker/action/conviction row
    within the last `within_hours`. Belt-and-suspenders: we also check
    alerts_fired in case the row landed there instead."""
    try:
        import db_pg
        cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM trade_recommendations
                    WHERE ticker = %s
                      AND COALESCE(action, '') = %s
                      AND COALESCE(conviction, '') = %s
                      AND created_at >= %s
                    LIMIT 1
                    """,
                    (ticker.upper(), action, conviction, cutoff),
                )
                if cur.fetchone():
                    return True
    except Exception as e:
        log.debug("scanner dedup query failed (%s); will allow alert", e)
    return False


def _was_ticker_recently_alerted(ticker: str,
                                 within_hours: int = DEFAULT_DEDUP_HOURS) -> bool:
    """True if ANY trade_recommendation for this ticker exists in the
    last `within_hours`. Ticker-level form for the pre-decide dedup
    (2026-06-10 cost fix) — we don't know the action/conviction before
    decide() runs, but we don't need to: if we pinged the operator about
    this ticker any time in the dedup window, skip the cycle's Haiku
    call entirely."""
    try:
        import db_pg
        cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM trade_recommendations
                    WHERE ticker = %s
                      AND created_at >= %s
                      AND COALESCE(conviction, '') IN ('Best Idea', 'Adding')
                    LIMIT 1
                    """,
                    (ticker.upper(), cutoff),
                )
                if cur.fetchone():
                    return True
    except Exception as e:
        log.debug("scanner ticker-dedup query failed (%s); will allow", e)
    return False


# ─────────────────────────── Pre-decide quick filter ─────────────────────
#
# Most tickers in the polling universe sit in mid_range with stable
# source flags for hours at a time. Calling decide() (a Haiku API call)
# for each of them on every 5-min scan is what was driving the API
# bill: ~231 calls/scan, ~95% of which deterministically short-circuited
# inside decide_notifier to action=WATCH on the mid_range gate. Move
# both checks BEFORE decide().

_SOURCE_FLAGS_STATE_KEY = "scanner_last_source_flags"


def _load_last_source_flags() -> dict[str, list[str]]:
    """Read the per-ticker source_flags snapshot from the prior scan.

    Stored as a JSON blob in bot_state under one row so the lookup is a
    single SELECT and the writeback a single INSERT … ON CONFLICT.
    Returns {} on absence / parse error so the caller always gets a
    usable dict."""
    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_state WHERE key = %s",
                            (_SOURCE_FLAGS_STATE_KEY,))
                row = cur.fetchone()
        if row and row[0]:
            data = json.loads(row[0])
            if isinstance(data, dict):
                return data
    except Exception as e:
        log.debug("scanner: source-flags state load failed (%s)", e)
    return {}


def _save_last_source_flags(state: dict[str, list[str]]) -> None:
    try:
        import db_pg
        payload = json.dumps(state, sort_keys=True)
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bot_state (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    (_SOURCE_FLAGS_STATE_KEY, payload),
                )
            conn.commit()
    except Exception as e:
        log.debug("scanner: source-flags state save failed (%s)", e)


def _quick_zone_for(ticker: str) -> str:
    """Compute the price_monitor zone for `ticker` without going through
    unified_refresh. Reads the latest RR row from db_pg (cheap) + a
    yfinance quote (cheap). Returns 'unknown' on any failure so the
    pre-filter conservatively keeps the ticker (no false positives that
    suppress a real edge alert)."""
    try:
        import db_pg
        from price_monitor import compute_zone, HEDGEYE_TO_YFINANCE, fetch_prices
        rr = db_pg.get_active_risk_range(ticker) if hasattr(db_pg, "get_active_risk_range") else None
        if not rr:
            # Fallback: pull from the bulk query (worst-case: one extra
            # SELECT, still cheap vs the Haiku call we're trying to avoid).
            for row in db_pg.get_active_risk_ranges():
                if row["ticker"] == ticker:
                    rr = row
                    break
        if not rr:
            return "unknown"
        lo = float(rr["buy_trade"])  if rr["buy_trade"]  is not None else None
        hi = float(rr["sell_trade"]) if rr["sell_trade"] is not None else None
        if lo is None or hi is None:
            return "unknown"
        yf_sym = HEDGEYE_TO_YFINANCE.get(ticker)
        if not yf_sym:
            return "unknown"
        prices = fetch_prices([yf_sym])
        price = prices.get(yf_sym)
        if price is None:
            return "unknown"
        return compute_zone(price, lo, hi)
    except Exception as e:
        log.debug("scanner: quick-zone for %s failed (%s)", ticker, e)
        return "unknown"


def _current_source_flags(ticker: str) -> list[str]:
    try:
        from tools.active_slice import source_flags_for
        return sorted(source_flags_for(ticker) or [])
    except Exception:
        return []


def _persist_recommendation(decision: dict) -> None:
    """Write the decision as a trade_recommendations row. Status='proposed'
    by default so the row is visible to any future approval UI."""
    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trade_recommendations
                        (ticker, direction, conviction, action,
                         recommended_dollars, reference_price,
                         reasoning, status, created_at, decided_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed', NOW(), %s)
                    """,
                    (
                        decision.get("ticker"),
                        decision.get("direction"),
                        decision.get("conviction"),
                        decision.get("action"),
                        decision.get("recommended_dollars"),
                        (decision.get("context_summary") or {}).get("yahoo_price"),
                        (decision.get("reasoning") or "")[:4000],
                        decision.get("decided_at"),
                    ),
                )
            conn.commit()
    except Exception as e:
        log.warning("scanner: persist trade_recommendations failed for %s: %s",
                    decision.get("ticker"), e)


# ─────────────────────────── Notification ───────────────────────────

def _format_alert(decision: dict) -> tuple[str, str]:
    """Returns (title, body) Telegram-friendly — 1-line notifier format
    (sizing-stripped, 2026-05-25). The notifier's `reasoning` field is
    already a fully-formed single line (price + range-position % + MFR
    trend + optional wall + one-line action context); the body is that
    line verbatim.

    Sizing decisions ($X / N bps) are explicitly NOT appended here — the
    operator handles position sizing themselves. The decision dict still
    carries `bps` and `recommended_dollars` for backend metrics, but they
    never appear in the outbound Telegram body.
    """
    ticker  = decision.get("ticker", "?")
    action  = decision.get("action", "WATCH")
    reasoning = (decision.get("reasoning") or "").strip()

    title = f"{action} {ticker}"
    body = reasoning if reasoning else f"{action} {ticker}"
    return title, body[:1024]


def _send_alert(decision: dict) -> bool:
    """Send the Telegram alert. Returns True on success."""
    title, body = _format_alert(decision)
    try:
        from notifier import send_telegram
        send_telegram(title, body)
        return True
    except Exception as e:
        log.warning("scanner: send_telegram failed for %s: %s",
                    decision.get("ticker"), e)
        return False


# ─────────────────────────── Per-ticker scan ───────────────────────────

def _quad_alignment(ticker: str, direction: Optional[str]) -> str:
    """'aligned' / 'counter' / 'neutral' — is the decision's direction
    consistent with the active Quad's favored longs/shorts universe?

    Membership test goes against the doctrine-universe ETF proxy
    (normalize_ticker → to_doctrine_proxy), not the raw RR/macro label
    (2026-06-10 fix — pre-fix the lookup compared 'GOLD'/'SPX' against
    a universe that only holds 'GLD'/'SPY' and always returned neutral).
    """
    try:
        from tools.doctrine import universe_for_quad
        from tools.ticker_aliases import normalize_ticker, to_doctrine_proxy
        q = _active_quad()
        longs = set(universe_for_quad(q, "longs"))
        shorts = set(universe_for_quad(q, "shorts"))
        raw = (ticker or "").upper()
        t = (to_doctrine_proxy(normalize_ticker(raw)) or raw).upper()
        d = (direction or "").lower()
        if d in ("long", "buy"):
            if t in longs:
                return "aligned"
            if t in shorts:
                return "counter"
        elif d in ("short", "sell", "close"):
            if t in shorts:
                return "aligned"
            if t in longs:
                return "counter"
        return "neutral"
    except Exception:
        return "neutral"


def _scan_one(ticker: str, *, dry_run: bool, refresh: bool,
              dedup_hours: int,
              prior_source_flags: dict[str, list[str]] | None = None,
              flags_state_out: dict[str, list[str]] | None = None) -> dict:
    """Evaluate a single ticker. Returns a result summary.

    Order matters (2026-06-10 cost fix):
      1. Ticker-level dedup BEFORE refresh + decide(). If we already
         pinged on this ticker in the dedup window, no Haiku call.
      2. Quick zone + source-flags pre-filter BEFORE refresh + decide().
         When the ticker is in mid_range AND its source flags haven't
         changed since the last scan, skip decide() — the deterministic
         mid_range gate inside decide_notifier was always going to
         short-circuit to WATCH anyway.
      3. Otherwise fall through to unified_refresh + decide() as before.
    """
    result = {
        "ticker": ticker,
        "actionable": False,
        "alerted": False,
        "deduped": False,
        "decision": None,
        "error": None,
    }

    # Step 1: ticker-level dedup — was this ticker pinged recently at
    # any conviction? If so we never need to look at it this cycle.
    if _was_ticker_recently_alerted(ticker, within_hours=dedup_hours):
        result["deduped"] = True
        result["skipped_reason"] = "ticker-recently-alerted"
        return result

    # Step 2: pre-decide quick filter — skip the Haiku call when the
    # ticker is in mid_range AND its source flags are identical to the
    # prior scan. Record current flags either way so the next scan can
    # diff against them.
    flags_now = _current_source_flags(ticker)
    if flags_state_out is not None:
        flags_state_out[ticker] = flags_now
    prior = (prior_source_flags or {}).get(ticker)
    if prior is not None and sorted(prior) == flags_now:
        zone = _quick_zone_for(ticker)
        if zone == "mid_range":
            result["skipped_reason"] = "mid_range + flags unchanged"
            return result

    # Step 3: full refresh + decide. Best-effort — even if a source is
    # stale, decision_engine will still get whatever's in the DB.
    if refresh:
        try:
            import unified_refresh
            unified_refresh.refresh_all_for_tickers(
                [ticker],
                capture_type="proactive_scan",
                spotgamma_reason="proactive_scan",
            )
        except Exception as e:
            log.warning("scanner: refresh failed for %s (continuing): %s", ticker, e)

    try:
        import decision_engine
        decision = decision_engine.decide(ticker, signal_origin="proactive_scan")
    except Exception as e:
        result["error"] = f"decide() raised: {e}"
        log.exception("scanner: decide() raised for %s", ticker)
        return result

    if not decision:
        result["error"] = "decide() returned None"
        return result

    result["decision"] = {
        "conviction": decision.get("conviction"),
        "direction":  decision.get("direction"),
        "action":     decision.get("action"),
        "bps":        decision.get("bps"),
        "dollars":    decision.get("recommended_dollars"),
        "confidence": decision.get("confidence"),
        "quad_alignment": _quad_alignment(ticker, decision.get("direction")),
        # Polling-universe source membership surfaced by decision_engine
        # (2026-05-25). Empty list when no source resolver was available.
        "source_flags":   decision.get("source_flags") or [],
        "quad_aligned":   bool(decision.get("quad_aligned")),
    }

    conviction = decision.get("conviction") or ""
    action     = decision.get("action") or ""
    if conviction not in ACTIONABLE_CONVICTIONS or action not in ACTIONABLE_ACTIONS:
        return result

    result["actionable"] = True

    if _was_recently_alerted(ticker, action, conviction, within_hours=dedup_hours):
        result["deduped"] = True
        return result

    if dry_run:
        return result

    _persist_recommendation(decision)
    if _send_alert(decision):
        result["alerted"] = True
    return result


# ─────────────────────────── Main entry ───────────────────────────

def scan(tickers: Optional[list[str]] = None,
         max_tickers: Optional[int] = None,
         dry_run: bool = False,
         refresh: bool = True,
         dedup_hours: int = DEFAULT_DEDUP_HOURS,
         throttle_seconds: float = 0.0,
         priority: str = "all",
         max_workers: int = 1,
         quad_filtered: bool = False,
         use_monthly_quad: bool = False,
         source: str = "monitored",
         lookback_days: int = 7) -> dict:
    """Run a proactive scan. Returns a summary dict.

    max_workers > 1 parallelises _scan_one across a ThreadPoolExecutor so
    a 50-ticker cycle completes in ~1 min instead of ~10 min serially. The
    Anthropic API tolerates 8 concurrent messages calls comfortably. The
    network-bound legs (yfinance / MFR) also parallelise well.
    """
    # Quad preflight — doctrine raises QuadUnsetError when neither bot_state
    # nor env supplies a Quad value (2026-06-10 fix: no silent Quad-1
    # default). One Telegram halt notice, return an empty-but-shaped
    # summary so the caller log lines stay legible.
    try:
        from tools.doctrine import (current_monthly_quad,
                                    current_quarterly_quad,
                                    QuadUnsetError)
        current_quarterly_quad()
        current_monthly_quad()
    except QuadUnsetError as e:
        log.error("scanner halted — Quad unset: %s", e)
        if not dry_run:
            try:
                from notifier import send_telegram
                send_telegram("QUAD UNSET — halted",
                              f"proactive_scanner skipping cycle: {e}")
            except Exception as exc:
                log.warning("Telegram halt notice failed: %s", exc)
        now = datetime.now(timezone.utc).isoformat()
        return {
            "started_at":  now,
            "finished_at": now,
            "watchlist":   [],
            "counts":      {"actionable": 0, "alerted": 0, "deduped": 0,
                            "errors": 0, "quad_unset": 1},
            "per_ticker":  [],
            "dry_run":     dry_run,
        }

    watchlist = _resolve_watchlist(tickers, max_tickers, priority=priority,
                                    quad_filtered=quad_filtered,
                                    use_monthly_quad=use_monthly_quad,
                                    source=source,
                                    lookback_days=lookback_days)
    started_at = datetime.now(timezone.utc)
    log.info("scanner: starting scan over %d tickers (dry_run=%s, priority=%s, workers=%d)",
             len(watchlist), dry_run, priority, max_workers)

    # Pre-decide filter state (2026-06-10 cost fix). Load the prior
    # scan's per-ticker source_flags so _scan_one can skip mid_range
    # tickers whose flags haven't changed. flags_state_out collects the
    # current cycle's flags for writeback after the scan.
    prior_source_flags = _load_last_source_flags()
    flags_state_out: dict[str, list[str]] = {}

    per_ticker: list = [None] * len(watchlist)
    counts = {"actionable": 0, "alerted": 0, "deduped": 0,
              "errors": 0, "skipped_quiet": 0}

    if max_workers and max_workers > 1 and watchlist:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(_scan_one, t, dry_run=dry_run, refresh=refresh,
                            dedup_hours=dedup_hours,
                            prior_source_flags=prior_source_flags,
                            flags_state_out=flags_state_out): i
                for i, t in enumerate(watchlist)
            }
            done_count = 0
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                ticker = watchlist[idx]
                try:
                    r = fut.result()
                except Exception as e:
                    log.exception("scanner: %s raised in worker: %s", ticker, e)
                    r = {"ticker": ticker, "actionable": False, "alerted": False,
                         "deduped": False, "error": str(e), "decision": None}
                per_ticker[idx] = r
                done_count += 1
                log.info("scanner: [%d/%d] %s done (actionable=%s)",
                         done_count, len(watchlist), ticker, r.get("actionable"))
                if r["actionable"]: counts["actionable"] += 1
                if r["alerted"]:    counts["alerted"] += 1
                if r["deduped"]:    counts["deduped"] += 1
                if r["error"]:      counts["errors"] += 1
                if r.get("skipped_reason") and not r["deduped"]:
                    counts["skipped_quiet"] += 1
    else:
        for i, ticker in enumerate(watchlist):
            log.info("scanner: [%d/%d] %s", i + 1, len(watchlist), ticker)
            r = _scan_one(ticker, dry_run=dry_run, refresh=refresh,
                         dedup_hours=dedup_hours,
                         prior_source_flags=prior_source_flags,
                         flags_state_out=flags_state_out)
            per_ticker[i] = r
            if r["actionable"]: counts["actionable"] += 1
            if r["alerted"]:    counts["alerted"] += 1
            if r["deduped"]:    counts["deduped"] += 1
            if r["error"]:      counts["errors"] += 1
            if r.get("skipped_reason") and not r["deduped"]:
                counts["skipped_quiet"] += 1
            if throttle_seconds and i < len(watchlist) - 1:
                time.sleep(throttle_seconds)

    # Persist this cycle's source_flags snapshot so the next scan can
    # diff against it. Best-effort — failure to write just degrades the
    # pre-filter to "always run decide()" until the next successful save.
    if flags_state_out and not dry_run:
        _save_last_source_flags(flags_state_out)

    summary = {
        "started_at":   started_at.isoformat(),
        "finished_at":  datetime.now(timezone.utc).isoformat(),
        "watchlist":    watchlist,
        "counts":       counts,
        "per_ticker":   per_ticker,
        "dry_run":      dry_run,
    }
    return summary


def _cli() -> int:
    ap = argparse.ArgumentParser(
        description="Proactive idea scanner — runs decision_engine across a watchlist"
    )
    ap.add_argument("--tickers", nargs="*",
                    help="Explicit tickers (overrides inventory)")
    ap.add_argument("--max-tickers", type=int, default=None,
                    help="Cap inventory scan length (default: no cap)")
    ap.add_argument("--dedup-hours", type=int, default=DEFAULT_DEDUP_HOURS,
                    help="Suppress identical (ticker,action,conviction) within N hours")
    ap.add_argument("--no-refresh", action="store_true",
                    help="Skip upstream refresh (MFR/SG/Yahoo) before each decide()")
    ap.add_argument("--throttle", type=float, default=0.0,
                    help="Sleep N seconds between tickers (rate limiting)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Decide but do not persist or notify")
    ap.add_argument("--priority", choices=("high","tail","all"), default="all",
                    help="Watchlist slice (default 'all'). 'high' = today's "
                         "Hedgeye-touched tickers; fits a sub-5-min cycle.")
    ap.add_argument("--workers", type=int, default=1,
                    help="ThreadPool workers for the per-ticker loop. 8 is "
                         "safe for the Anthropic API; default 1 = serial.")
    ap.add_argument("--source",
                    choices=("polling_universe", "active_slice",
                             "hedgeye_active", "quad", "monitored"),
                    default="polling_universe",
                    help="Universe source. 'polling_universe' (default) = "
                         "the full union the bot is supposed to monitor: "
                         "Quad slice + ETF Pro long/short + Signal Strength "
                         "+ Portfolio Solutions + Risk Range + operator "
                         "overrides. 'active_slice' = Quad-only slice (the "
                         "previous default; still useful for Quad-only "
                         "runs). 'hedgeye_active' = tickers Keith is "
                         "actively surfacing through Hedgeye products in "
                         "the last --lookback-days (legacy). 'quad' = "
                         "doctrine Quad universe. 'monitored' = legacy "
                         "inventory view sliced by --priority.")
    ap.add_argument("--lookback-days", type=int, default=7,
                    help="Lookback window for --source hedgeye_active "
                         "(default: 7).")
    ap.add_argument("--quad-filtered", action="store_true",
                    help="Back-compat alias for --source quad: Hedgeye "
                         "favored longs+shorts for the active Quad "
                         "(strategic/quarterly).")
    ap.add_argument("--use-monthly-quad", action="store_true",
                    help="With --quad-filtered, use the MONTHLY (tactical) "
                         "Quad instead of the quarterly one.")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    summary = scan(
        tickers=args.tickers,
        max_tickers=args.max_tickers,
        dry_run=args.dry_run,
        refresh=not args.no_refresh,
        dedup_hours=args.dedup_hours,
        throttle_seconds=args.throttle,
        priority=args.priority,
        max_workers=args.workers,
        quad_filtered=args.quad_filtered,
        use_monthly_quad=args.use_monthly_quad,
        source=("quad" if args.quad_filtered else args.source),
        lookback_days=args.lookback_days,
    )
    print(json.dumps(summary, indent=2, default=str), flush=True)
    sys.stdout.flush()
    return 0 if summary["counts"]["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(_cli())
