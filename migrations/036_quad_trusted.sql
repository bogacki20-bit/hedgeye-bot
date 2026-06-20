-- ============================================================================
-- Migration 036 — quarantine off-by-one quad on hedgeye_portfolio_actions
--
-- The legacy quad source stamped portfolio-action rows with an off-by-one quad
-- (monthly stored "Quad 2" / quarterly "Quad 4" vs. the dashboard truth
-- "Quad 1" / "Quad 3"). As of 2026-06-19 the dashboard scrape (hedgeye_quad.py)
-- is canonical: it writes quad_regime_history with source='hedgeye_quad_dashboard',
-- so current_quad_regime() — and therefore new portfolio-action stamps — are now
-- correct going forward.
--
-- This migration does NOT delete or re-stamp any quad value. It only adds a
-- trust flag so the already-poisoned rows can be excluded from training/analysis
-- until a separate, deliberate backfill corrects them.
--
--   new rows  -> quad_trusted = true  (column default; insert_actions needs no change)
--   pre-fix   -> quad_trusted = false (the one-time backfill below)
--
-- Applied to production 2026-06-19: 636 rows flagged false (all rows existed
-- before the fix; max captured_at was 2026-06-18 19:33 UTC). 0 trusted at apply.
--
-- Apply via apply_migration.py. All statements idempotent — safe to re-run.
-- ============================================================================

-- ── trust flag ──────────────────────────────────────────────────────────────
ALTER TABLE hedgeye_portfolio_actions
    ADD COLUMN IF NOT EXISTS quad_trusted boolean NOT NULL DEFAULT true;

-- ── one-time backfill: flag every pre-fix row as untrusted ──────────────────
-- Scoped by captured_at so a re-run never re-flags rows stamped after the fix
-- went live; the IS DISTINCT FROM guard makes repeated runs a no-op. The cutoff
-- is the UTC day the scrape became canonical — every pre-fix row predates it.
UPDATE hedgeye_portfolio_actions
    SET quad_trusted = false
    WHERE captured_at < TIMESTAMPTZ '2026-06-19 00:00:00+00'
      AND quad_trusted IS DISTINCT FROM false;
