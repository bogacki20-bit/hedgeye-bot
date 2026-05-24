"""parser_t1a.py — Tier 1 Alpha GEX-L1 ingestion stub.

PLACEHOLDER as of 2026-05-24. The notifier-rollback ships without T1A
integration; this stub exists so future-self can find the slot. Next
weekend scope: implement T1A daily scrape, normalize the GEX-L1 surface,
write a t1a_snapshots table.

TODO:
  - Confirm T1A product the operator subscribes to and its delivery channel
    (email, CSV download, REST?). Likely email; scrape pattern matches the
    other Hedgeye parsers.
  - Schema: t1a_snapshots(ticker, snapshot_date, gex_l1, ...).
  - Hook into unified_refresh as a fourth leg (after MFR/SG/Yahoo) gated
    by env flag T1A_ENABLED so the existing pipeline doesn't break if
    the source goes dark.
  - Live-path read: optionally fold GEX-L1 into the notifier prompt as a
    one-line tag once the ingestion has been stable for a week.

See TIER1_ALPHA_TODO.md for the longer plan.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def parse(*args, **kwargs):
    """Stub. Returns None so any future caller that wires this in by mistake
    gets a clean no-op rather than a crash."""
    log.debug("parser_t1a.parse called but T1A ingestion is not implemented yet")
    return None
