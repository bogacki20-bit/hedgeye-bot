-- ============================================================================
-- Migration 002 — dry-run decision framework
-- Phase 1.5: extends alerts_fired with recommendation context, adds
-- user_actions table (decisions captured from Telegram replies), and
-- outcome_followups table (1d/5d/20d outcome tracking per user action).
--
-- Apply via:
--   railway run psql $DATABASE_URL -f migrations/002_dry_run_framework.sql
-- All statements are idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- Re-running is safe.
-- ============================================================================

ALTER TABLE alerts_fired
    ADD COLUMN IF NOT EXISTS recommendation_text TEXT,
    ADD COLUMN IF NOT EXISTS suggested_action    TEXT,
    ADD COLUMN IF NOT EXISTS suggested_dollars   NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS framework_alignment TEXT,
    ADD COLUMN IF NOT EXISTS hedgeye_context     JSONB,
    ADD COLUMN IF NOT EXISTS spotgamma_context   JSONB;


CREATE TABLE IF NOT EXISTS user_actions (
    id                       BIGSERIAL PRIMARY KEY,
    alert_id                 BIGINT REFERENCES alerts_fired(id) ON DELETE SET NULL,
    ticker                   TEXT NOT NULL,
    decided_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decision                 TEXT NOT NULL,
    executed                 BOOLEAN NOT NULL DEFAULT FALSE,
    executed_action          TEXT,
    executed_dollars         NUMERIC(14,2),
    executed_shares          NUMERIC(14,4),
    executed_price           NUMERIC(14,4),
    account                  TEXT,
    fidelity_confirmation_id TEXT,
    notes                    TEXT,
    raw_telegram_text        TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_actions_alert    ON user_actions(alert_id);
CREATE INDEX IF NOT EXISTS idx_actions_ticker   ON user_actions(ticker);
CREATE INDEX IF NOT EXISTS idx_actions_decided  ON user_actions(decided_at);
CREATE INDEX IF NOT EXISTS idx_actions_decision ON user_actions(decision);


CREATE TABLE IF NOT EXISTS outcome_followups (
    id                  BIGSERIAL PRIMARY KEY,
    action_id           BIGINT NOT NULL REFERENCES user_actions(id) ON DELETE CASCADE,
    days_after          INTEGER NOT NULL,
    measured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    price_at_followup   NUMERIC(14,4),
    pnl_dollars         NUMERIC(14,2),
    pnl_pct             NUMERIC(8,4),
    notes               TEXT,
    UNIQUE (action_id, days_after)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_action ON outcome_followups(action_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_days   ON outcome_followups(days_after);
