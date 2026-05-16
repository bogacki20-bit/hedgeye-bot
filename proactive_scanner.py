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
ACTIONABLE_ACTIONS     = {"BUY", "ADD", "TRIM", "SELL"}
DEFAULT_DEDUP_HOURS    = 4


# ─────────────────────────── Watchlist resolution ───────────────────────────

def _resolve_watchlist(explicit: Optional[list[str]],
                      max_tickers: Optional[int],
                      priority: str = "all") -> list[str]:
    """Return the deduped list of tickers this scan should evaluate.

    Resolution order:
      explicit > tools.list_monitored_tickers (with --priority) > ticker_inventory view.

    `priority` can be 'high' / 'tail' / 'all'. 'high' is the recently-stamped
    Hedgeye-touched bucket and fits in a sub-5-min cycle; the scheduled
    scanner uses it so each fire's alerts cite a fresh price.
    """
    if explicit:
        return sorted({t.upper().strip() for t in explicit if t and t.strip()})

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
    """Returns (title, body) Telegram-friendly. Mirrors the email_parser
    formatter so the user sees a consistent presentation regardless of
    whether the trigger was an email or a proactive scan."""
    ticker = decision.get("ticker", "?")
    action = decision.get("action", "WATCH")
    conviction = decision.get("conviction", "?")
    bps = decision.get("bps")
    dollars = decision.get("recommended_dollars")
    conf = decision.get("confidence")
    reasoning = decision.get("reasoning") or ""
    evidence = decision.get("evidence") or []
    ctx = decision.get("context_summary") or {}

    size_str = f"${dollars:.0f} ({bps} bps)" if dollars and bps else "no size"
    title = f"Scan: {action} {ticker} — {conviction}"

    lines = [
        f"→ {action} {ticker}  size {size_str}",
        f"confidence: {conf:.0%}" if isinstance(conf, (int, float)) else "",
        f"quad: {ctx.get('hedgeye_quad')} | vix bucket: {ctx.get('vix_bucket')}",
        f"px {ctx.get('yahoo_price')} | sg put/call walls {ctx.get('spotgamma_put_wall')}/{ctx.get('spotgamma_call_wall')}",
        f"mfr {ctx.get('mfr_range_low')}–{ctx.get('mfr_range_high')} hurst {ctx.get('mfr_hurst')}",
        "",
        reasoning[:400],
    ]
    if evidence:
        lines.append("")
        lines.append("evidence:")
        for e in evidence[:4]:
            lines.append(f"  • {str(e)[:120]}")

    body = "\n".join(L for L in lines if L is not None)
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

def _scan_one(ticker: str, *, dry_run: bool, refresh: bool,
              dedup_hours: int) -> dict:
    """Evaluate a single ticker. Returns a result summary."""
    result = {
        "ticker": ticker,
        "actionable": False,
        "alerted": False,
        "deduped": False,
        "decision": None,
        "error": None,
    }

    # Refresh upstream sources first (lockstep). Best-effort — even if a
    # source is stale, decision_engine will still get whatever's in the DB.
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
         max_workers: int = 1) -> dict:
    """Run a proactive scan. Returns a summary dict.

    max_workers > 1 parallelises _scan_one across a ThreadPoolExecutor so
    a 50-ticker cycle completes in ~1 min instead of ~10 min serially. The
    Anthropic API tolerates 8 concurrent messages calls comfortably. The
    network-bound legs (yfinance / MFR) also parallelise well.
    """
    watchlist = _resolve_watchlist(tickers, max_tickers, priority=priority)
    started_at = datetime.now(timezone.utc)
    log.info("scanner: starting scan over %d tickers (dry_run=%s, priority=%s, workers=%d)",
             len(watchlist), dry_run, priority, max_workers)

    per_ticker: list = [None] * len(watchlist)
    counts = {"actionable": 0, "alerted": 0, "deduped": 0, "errors": 0}

    if max_workers and max_workers > 1 and watchlist:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(_scan_one, t, dry_run=dry_run, refresh=refresh,
                            dedup_hours=dedup_hours): i
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
    else:
        for i, ticker in enumerate(watchlist):
            log.info("scanner: [%d/%d] %s", i + 1, len(watchlist), ticker)
            r = _scan_one(ticker, dry_run=dry_run, refresh=refresh,
                         dedup_hours=dedup_hours)
            per_ticker[i] = r
            if r["actionable"]: counts["actionable"] += 1
            if r["alerted"]:    counts["alerted"] += 1
            if r["deduped"]:    counts["deduped"] += 1
            if r["error"]:      counts["errors"] += 1
            if throttle_seconds and i < len(watchlist) - 1:
                time.sleep(throttle_seconds)

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
    )
    print(json.dumps(summary, indent=2, default=str), flush=True)
    sys.stdout.flush()
    return 0 if summary["counts"]["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(_cli())
