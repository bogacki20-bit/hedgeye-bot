-- Migration 010 — outcomes_log: realized round-trip P&L for ML labels.
--
-- One row per closed (FIFO-matched) round trip built by
-- tools/compute_outcomes.py from actions_log. Optionally links back to
-- the alerts_fired row that preceded the opening trade (within 24h) so
-- every outcome has its decision context for training.
--
-- Idempotent, additive only.

CREATE TABLE IF NOT EXISTS outcomes_log (
    id                     BIGSERIAL PRIMARY KEY,
    alert_id               BIGINT REFERENCES alerts_fired(id) ON DELETE SET NULL,
    action_id_open         BIGINT NOT NULL REFERENCES actions_log(id) ON DELETE CASCADE,
    action_id_close        BIGINT NOT NULL REFERENCES actions_log(id) ON DELETE CASCADE,
    ticker                 TEXT,
    normalized_ticker      TEXT,
    account_number         TEXT,
    entry_price            NUMERIC(14,4),
    exit_price             NUMERIC(14,4),
    qty                    NUMERIC(18,6),
    holding_period_minutes BIGINT,
    pnl_dollars            NUMERIC(16,2),
    pnl_pct                NUMERIC(10,4),
    was_winner             BOOLEAN,
    quad_regime_at_open    TEXT,
    quad_regime_at_close   TEXT,
    closed_at              TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (action_id_open, action_id_close)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_ticker  ON outcomes_log(normalized_ticker);
CREATE INDEX IF NOT EXISTS idx_outcomes_account ON outcomes_log(account_number);
CREATE INDEX IF NOT EXISTS idx_outcomes_closed  ON outcomes_log(closed_at);
