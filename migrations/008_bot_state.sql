-- Migration 008 — bot_state: durable key/value for cross-run bot state.
--
-- Holds the live multi-timeframe Quad regime detected by
-- tools/detect_quads.py:
--   current_quarterly_quad      "Quad 1".."Quad 4"  (strategic)
--   current_monthly_quad        "Quad 1".."Quad 4"  (tactical)
--   last_quad_detection_at      ISO timestamp of the last detector run
--
-- tools/doctrine.py reads these (env overrides
-- CURRENT_QUARTERLY_QUAD_OVERRIDE / CURRENT_MONTHLY_QUAD_OVERRIDE take
-- precedence). Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS bot_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
