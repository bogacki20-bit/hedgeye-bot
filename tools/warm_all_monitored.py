"""Drive unified_refresh over the full monitored_tickers list so every
Hedgeye-surfaced ticker lands in MFR + SpotGamma + Yahoo data, regardless of
whether it happened to be mentioned in today's email flow or scanned by the
market-hours proactive_scanner.

The Hedgeye Bot's day looks like this without this helper:
  • Email arrivals → unified_refresh fan-out only for tickers in that email.
  • Scanner (market hours, every 30 min, --max-tickers N) → on-demand
    yfinance fetches for the first N tickers in monitored_tickers.
  • Everything else in the ~278-ticker watchlist drifts out of date.

This helper closes the gap. Intended to be invoked once per evening (or
pre-market) via Task Scheduler / Cowork SKILL. Chunks the watchlist so a
single subprocess.run timeout doesn't blow up the whole sweep.

Priority axis (same as tools.list_monitored_tickers):
  --priority high   today's Hedgeye-touched tickers
  --priority tail   monitored_tickers minus today's high set
  --priority all    everything (default)

Usage examples
--------------
  # Dry-run: print the plan, refresh nothing
  python -m tools.warm_all_monitored --priority all --dry-run

  # Real run, 25 tickers per unified_refresh call, throttle 5s between calls
  python -m tools.warm_all_monitored --priority all --batch-size 25 --throttle 5

  # High-priority only (pre-market)
  python -m tools.warm_all_monitored --priority high
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import List

log = logging.getLogger(__name__)


def _load_tickers(priority: str) -> List[str]:
    """Reuse the helper module so we keep the single source of truth."""
    from tools import list_monitored_tickers as L
    if priority == "high":
        return L.fetch_high()
    if priority == "tail":
        return L.fetch_tail()
    return L.fetch_all()


def _chunks(seq: List[str], n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def warm(tickers: List[str], *, batch_size: int, throttle_s: float,
         capture_type: str = "on_demand",
         spotgamma_reason: str = "warm_sweep",
         dry_run: bool = False) -> dict:
    """Refresh `tickers` in batches via unified_refresh.

    Returns an aggregated summary:
        {
          "batches": [{"tickers": [...], "summary": {...}}, ...],
          "totals": {"mfr_ok": N, "mfr_fail": N, "sg_queued": N, "yf_ok": N, "yf_fail": N},
          "skipped": False,
        }
    """
    out = {"batches": [], "totals": {
        "mfr_ok": 0, "mfr_fail": 0,
        "sg_queued": 0, "sg_queue_size": 0,
        "yf_ok": 0, "yf_fail": 0,
    }}
    if not tickers:
        out["skipped"] = True
        return out

    if dry_run:
        out["dry_run"] = True
        out["plan"] = {
            "ticker_count": len(tickers),
            "batches": (len(tickers) + batch_size - 1) // batch_size,
            "batch_size": batch_size,
            "throttle_s": throttle_s,
        }
        return out

    import unified_refresh

    for i, batch in enumerate(_chunks(tickers, batch_size), 1):
        log.info("warm: batch %d (%d tickers): %s", i, len(batch), ",".join(batch))
        summary = unified_refresh.refresh_all_for_tickers(
            batch,
            capture_type=capture_type,
            spotgamma_reason=spotgamma_reason,
        )
        mfr = summary.get("mfr") or {}
        sg = summary.get("spotgamma") or {}
        yf = summary.get("yfinance") or {}
        out["totals"]["mfr_ok"]   += int(mfr.get("ok") or 0)
        out["totals"]["mfr_fail"] += int(mfr.get("fail") or 0)
        out["totals"]["sg_queued"]     += int(sg.get("queued") or 0)
        out["totals"]["sg_queue_size"]  = int(sg.get("queue_size") or out["totals"]["sg_queue_size"])
        out["totals"]["yf_ok"]   += int(yf.get("ok") or 0)
        out["totals"]["yf_fail"] += int(yf.get("fail") or 0)
        out["batches"].append({"tickers": batch, "summary": summary})

        if throttle_s and throttle_s > 0:
            time.sleep(throttle_s)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--priority", choices=("high", "tail", "all"), default="all")
    ap.add_argument("--batch-size", type=int, default=25,
                    help="Tickers per unified_refresh call (default 25)")
    ap.add_argument("--throttle", type=float, default=5.0,
                    help="Seconds to sleep between batches (default 5)")
    # yfinance_client.fetch_and_save accepts: email_arrival, monitor_cycle,
    # on_demand. Anything else logs a warning but still writes. Use on_demand
    # as the semantic match for a scheduled bulk warm.
    ap.add_argument("--capture-type", default="on_demand",
                    help="Tag for yahoo_snapshots (default on_demand)")
    ap.add_argument("--spotgamma-reason", default="warm_sweep",
                    help="Tag for the SG refresh queue (default warm_sweep)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan, refresh nothing")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    tickers = _load_tickers(args.priority)
    result = warm(
        tickers,
        batch_size=args.batch_size,
        throttle_s=args.throttle,
        capture_type=args.capture_type,
        spotgamma_reason=args.spotgamma_reason,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
