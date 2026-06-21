-- ============================================================================
-- Migration 038 — SpotGamma Equity Hub CSV columns on spotgamma_snapshots
--
-- The capture-frame SpotGamma plug-in ingests the Equity Hub "All Symbols" CSV
-- export (capture_type='equityhub_csv', ~4,941 rows). 25 CSV fields map onto
-- existing spotgamma_snapshots columns; these 14 had no home. All numeric,
-- nullable, additive — existing rows and the canary's eod/premarket writes are
-- left untouched.
--
-- Deliberately NOT added:
--   * "Earnings Date" — the CSV emits "MM-DD H:MM AM/PM" (no year), which does
--     not fit the existing earnings_date DATE column; left NULL for equityhub_csv
--     rows rather than silently coerced.
--   * "isWatchlisted" — UI personalization flag, not stored.
--   * gamma_hedge_est is absent from this CSV and stays NULL for these rows.
--
-- Parser note: the CSV exports negatives as text with a leading apostrophe
-- (e.g. '-2662371); the plug-in strips it before the numeric cast.
--
-- Applied 2026-06-20. Apply via apply_migration.py. Idempotent — safe to re-run.
-- ============================================================================

ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS options_impact        numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS next_exp_gamma        numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS next_exp_delta        numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS next_exp_call_vol     numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS next_exp_put_vol      numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS volume_ratio          numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS gamma_ratio           numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS delta_ratio           numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS ne_skew               numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS skew                  numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS dpi                   numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS dpi_volume_pct        numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS dpi_5day              numeric;
ALTER TABLE spotgamma_snapshots ADD COLUMN IF NOT EXISTS dpi_5day_volume_pct   numeric;
