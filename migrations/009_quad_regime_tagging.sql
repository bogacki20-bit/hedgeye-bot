-- Migration 009 — Quad regime tagging on alerts_fired + actions_log.
--
-- Stamps the active Hedgeye Quad at the time of each alert / action so
-- ML training and EOW reports can slice performance by macro regime.
-- Idempotent, additive only.

ALTER TABLE alerts_fired
    ADD COLUMN IF NOT EXISTS quad_regime TEXT;

ALTER TABLE actions_log
    ADD COLUMN IF NOT EXISTS quad_regime TEXT;
