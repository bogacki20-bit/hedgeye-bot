-- ============================================================================
-- Migration 069 — volume decel-day streak counter (2026-07-19)
--
-- Adds decel_streak to volume_snapshots: the number of consecutive sessions
-- ending today that are "decelerating" (down day AND volume below the prior
-- day). Day 3 of decelerating volume on a dip is a far stronger exhaustion
-- read than day 1 — pairs with real_dip + PASS_THE_PUCK for high conviction.
--
-- Additive (ADD COLUMN IF NOT EXISTS) — safe to re-run.
-- Apply via:  py apply_migration.py migrations/069_volume_decel_streak.sql
-- ============================================================================

ALTER TABLE volume_snapshots
    ADD COLUMN IF NOT EXISTS decel_streak INTEGER;
