"""Unified refresh orchestrator — fans a Hedgeye-originated ticker list out to
MFR + SpotGamma + Yahoo in one call.

Per the north-star architecture (memory: project_north_star_architecture.md):
Hedgeye is the signal source; MFR / SpotGamma / Yahoo are tools that update
in lockstep when Hedgeye surfaces a ticker. This module is the entry point
that enforces that lockstep — one call site for parsers and any future
trigger (cron, manual, REPL).

Returns a combined summary dict with per-source breakdowns:
    {
      "tickers": ["OIH", "XOP"],
      "mfr":       {"tickers": 2, "ok": 2, "fail": 0},
      "spotgamma": {"queued": 0, "queue_size": 0, "stale": []},
      "yfinance":  {"tickers": 2, "ok": 2, "fail": 0},
    }

Each source call is wrapped — a failure in one (e.g. SpotGamma queue write
permission error) does not prevent the others from running. Failed sources
return {"error": "..."} and downstream callers can decide whether that's
fatal for their use case.

Usage:
    import unified_refresh
    summary = unified_refresh.refresh_all_for_tickers(
        ["OIH", "XOP"],
        capture_type="email_arrival",
        spotgamma_reason="hedgeye_email:rta",
    )

CLI smoke test:
    py unified_refresh.py --tickers OIH XOP
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Optional

log = logging.getLogger(__name__)


def refresh_all_for_tickers(
    tickers: list[str],
    *,
    capture_type: str = "email_arrival",
    spotgamma_reason: Optional[str] = None,
    skip_spotgamma_if_fresh: bool = True,
) -> dict:
    """Refresh MFR + SpotGamma + Yahoo for `tickers` in lockstep.

    Args:
        tickers: ticker symbols to refresh. Deduped + uppercased internally.
        capture_type: passed to yfinance_client.refresh_for_tickers
            (and reserved for future per-source tagging).
        spotgamma_reason: stamped on the SpotGamma queue entry so the SKILL's
            Telegram ping shows *which* trigger surfaced this ticker
            (e.g. 'hedgeye_email:rta' / 'risk_range' / 'manual').
            Defaults to 'unified_refresh' if not provided.
        skip_spotgamma_if_fresh: when True (default), only queue tickers whose
            spotgamma_snapshots row is missing or older than the staleness
            threshold. Set False to force a refresh of every listed ticker.

    Returns a summary dict with per-source breakdowns. Never raises — each
    source call is independently wrapped so one failure doesn't block the
    others.
    """
    cleaned = sorted({t.upper().strip() for t in (tickers or []) if t and t.strip()})
    summary: dict = {
        "tickers": cleaned,
        "mfr":       {},
        "spotgamma": {},
        "yfinance":  {},
    }
    if not cleaned:
        return summary

    # ─── MFR ────────────────────────────────────────────────────────────
    try:
        import mfr_client
        summary["mfr"] = mfr_client.refresh_for_tickers(cleaned)
    except Exception as e:
        log.warning("unified_refresh: mfr leg failed: %s", e)
        summary["mfr"] = {"error": str(e)}

    # ─── SpotGamma ──────────────────────────────────────────────────────
    try:
        import spotgamma_client
        if skip_spotgamma_if_fresh:
            stale = spotgamma_client.tickers_needing_refresh(cleaned)
        else:
            stale = list(cleaned)
        if stale:
            sg_result = spotgamma_client.queue_refresh(
                stale, reason=spotgamma_reason or "unified_refresh",
            )
            sg_result["stale"] = stale
            summary["spotgamma"] = sg_result
        else:
            summary["spotgamma"] = {"queued": 0, "queue_size": 0, "stale": []}
    except Exception as e:
        log.warning("unified_refresh: spotgamma leg failed: %s", e)
        summary["spotgamma"] = {"error": str(e)}

    # ─── Yahoo ──────────────────────────────────────────────────────────
    try:
        import yfinance_client
        summary["yfinance"] = yfinance_client.refresh_for_tickers(
            cleaned, capture_type=capture_type,
        )
    except Exception as e:
        log.warning("unified_refresh: yfinance leg failed: %s", e)
        summary["yfinance"] = {"error": str(e)}

    return summary


def _format_summary_log(summary: dict) -> str:
    """Compact one-line summary for log emission by parsers."""
    mfr = summary.get("mfr") or {}
    sg = summary.get("spotgamma") or {}
    yf = summary.get("yfinance") or {}

    def _src(d: dict, ok_key: str, *, fallback_key: Optional[str] = None) -> str:
        if "error" in d:
            return f"err({d['error'][:40]})"
        if ok_key in d:
            return f"ok={d.get(ok_key, 0)}/fail={d.get('fail', 0)}"
        if fallback_key and fallback_key in d:
            return f"queued={d.get(fallback_key, 0)}"
        return "n/a"

    return (
        f"unified_refresh: {len(summary.get('tickers') or [])} ticker(s) — "
        f"mfr {_src(mfr, 'ok')} | "
        f"spotgamma {_src(sg, 'queued', fallback_key='queued')} | "
        f"yahoo {_src(yf, 'ok')}"
    )


def _cli() -> None:
    ap = argparse.ArgumentParser(
        description="unified_refresh smoke test — fan out MFR + SpotGamma + Yahoo for tickers"
    )
    ap.add_argument("--tickers", nargs="+", required=True,
                    help="Ticker symbols to refresh")
    ap.add_argument("--capture-type", default="on_demand",
                    choices=["email_arrival", "monitor_cycle", "on_demand"])
    ap.add_argument("--reason", default="cli_smoke_test",
                    help="SpotGamma queue reason annotation")
    ap.add_argument("--force-spotgamma", action="store_true",
                    help="Skip the staleness check; queue every ticker for SpotGamma refresh")
    args = ap.parse_args()

    summary = refresh_all_for_tickers(
        args.tickers,
        capture_type=args.capture_type,
        spotgamma_reason=args.reason,
        skip_spotgamma_if_fresh=not args.force_spotgamma,
    )
    print(json.dumps(summary, indent=2, default=str))
    print()
    print(_format_summary_log(summary))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
