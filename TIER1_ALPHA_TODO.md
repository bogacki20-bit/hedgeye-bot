# Tier 1 Alpha (GEX-L1) ingestion — next-weekend plan

Captured 2026-05-24 during the notifier-rollback. T1A integration is the
next feature after the rollback ships and is verified for a week. It is
NOT part of the rollback itself.

## Scope

Ingest the Tier 1 Alpha daily GEX-L1 (gamma exposure, level 1) feed and
optionally fold one line of it into the Haiku notifier prompt as
corroboration on tight setups. Treat it the way the rollback now treats
MFR — a single signal source, no doctrine block, just a number the
notifier can reference.

## Open questions for the operator

1. **Delivery channel.** Is T1A delivered by email (subject prefix to
   match?), by daily download (where on disk?), or via an API?
2. **Subscription tier.** Which T1A product? GEX-L1 only, or the full
   surface (L0..L5, hedge band, etc.)?
3. **Universe.** Same as MFR's ~300, or a tighter tier-1 watchlist?
4. **Latency.** Daily snapshot only, or intraday refresh?

## Implementation sketch

1. `parser_t1a.py` (already a stub) — `parse(email_or_payload)` that
   normalizes to `{ticker, snapshot_date, gex_l1, gex_l0, hedge_band, ...}`.
2. `migrations/0XX_t1a_snapshots.sql` — `t1a_snapshots(ticker text, snapshot_date date, gex_l1 numeric, …, PRIMARY KEY (ticker, snapshot_date))`.
3. Wire into `unified_refresh.refresh_all_for_tickers` as a fourth leg
   behind `T1A_ENABLED=1` so the existing pipeline can't regress.
4. `decision_engine.decide_notifier` — add one line:
   `T1A GEX-L1: {gex_l1}` after the MFR block. Keep the prompt under
   12 lines.
5. Backfill recent days from whatever historical T1A data the operator
   has on hand.

## Don't break

- The bot must continue to ship trades even if T1A goes dark (network,
  scrape pattern change, subscription lapse). Gate the read with an env
  flag and tolerate missing rows.
- Don't touch `compute_outcomes.py` or the ML pipeline tables — they're
  the foundation and survive any feature change.
