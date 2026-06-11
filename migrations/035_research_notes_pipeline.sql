-- ============================================================================
-- Migration 035 — research-notes pipeline fixes (2026-06-11)
--
-- Four breakdowns this migration supports:
--   (1) Quad extractor read the tilt-FROM value, not the tilt-TO destination.
--       Add a structured tilt_target_quads JSONB so the parser can record the
--       per-period destination Quad ({effective_monthly, effective_quarterly,
--       effective_global, monthly_effective_from, quarterly_effective_from}).
--   (2) Regime updater needs a forward-date so a "Quad 4 for July" tilt only
--       flips bot_state.monthly_quad on July 1. effective_from_date holds the
--       monthly forward-switch date (NULL = apply immediately).
--   (4) PDF deck downloader needs somewhere to record the deck URL discovered
--       in the email body. full_report_url holds it (NULL until discovered).
--
-- The legacy `quad` column is kept for backward compatibility (it holds the
-- first Quad mention; new consumers should read tilt_target_quads instead).
--
-- Apply via:  py apply_migration.py migrations/035_research_notes_pipeline.sql
-- All statements idempotent — safe to re-run.
-- ============================================================================

ALTER TABLE hedgeye_research_notes
    ADD COLUMN IF NOT EXISTS tilt_target_quads   JSONB,
    ADD COLUMN IF NOT EXISTS effective_from_date DATE,
    ADD COLUMN IF NOT EXISTS full_report_url     TEXT;

-- Index so the regime updater can cheaply find the most recent row that
-- actually carries a tilt target.
CREATE INDEX IF NOT EXISTS idx_rn_tilt
    ON hedgeye_research_notes (signal_date DESC)
    WHERE tilt_target_quads IS NOT NULL;
