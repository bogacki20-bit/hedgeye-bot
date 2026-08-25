-- 078_mfr_backlog_snapshots.sql — make "same backlog as yesterday" checkable.
--
-- 2026-08-24: the operator reported "the backlog keeps giving me the same list
-- twice". The 8/18 diagnostic and the 8/24 one both found the backlog CORRECT
-- (unchanged inputs -> unchanged answer), but nothing stores yesterday's
-- backlog, so the claim was unfalsifiable either way. One row per day fixes
-- that: MFR COVERAGE prints a delta line against the previous stored day.
--
-- Written by the nightly enrollment job and by MFR COVERAGE (upsert — last
-- writer wins within a day). Read-only otherwise; feeds no gating or sizing.

CREATE TABLE IF NOT EXISTS mfr_backlog_snapshots (
  snapshot_date  date PRIMARY KEY,
  universe_count int  NOT NULL,
  enrolled_count int  NOT NULL,
  served_count   int  NOT NULL,
  backlog_count  int  NOT NULL,
  backlog        text NOT NULL,          -- space-separated tickers
  created_at     timestamptz NOT NULL DEFAULT now()
);
