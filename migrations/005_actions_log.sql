-- ============================================================================
-- 005 — actions_log: durable record of every Fidelity trade ingested from
--       Accounts_History CSV exports. Joined against alerts_fired to produce
--       the ACTED / SKIPPED / UNPROMPTED reconciliation report.
--
-- Idempotency via composite UNIQUE (run_date, account_number, raw_symbol,
-- qty, price, amount). Re-ingesting the same CSV is safe.
-- ============================================================================

CREATE TABLE IF NOT EXISTS actions_log (
    id                BIGSERIAL PRIMARY KEY,
    run_date          DATE NOT NULL,
    account_name      TEXT,
    account_number    TEXT NOT NULL,
    action            TEXT NOT NULL,                  -- "YOU BOUGHT ..." raw
    raw_symbol        TEXT,                            -- symbol as Fidelity wrote it
    normalized_symbol TEXT,                            -- via tools.ticker_aliases
    qty               NUMERIC,
    price             NUMERIC,
    amount            NUMERIC,
    commission        NUMERIC,
    fees              NUMERIC,
    type              TEXT,                            -- Cash / Margin
    source            TEXT NOT NULL DEFAULT 'fidelity_csv',
    raw_row           JSONB,
    settlement_date   DATE,
    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Composite uniqueness so re-ingesting the same CSV doesn't double-count.
-- NULLs in any included column won't dedupe, so coalesce raw_symbol+qty+price
-- to known sentinels.
CREATE UNIQUE INDEX IF NOT EXISTS uq_actions_log_natural
    ON actions_log (
        run_date,
        account_number,
        COALESCE(raw_symbol, ''),
        COALESCE(qty,    0),
        COALESCE(price,  0),
        COALESCE(amount, 0)
    );

CREATE INDEX IF NOT EXISTS ix_actions_log_normalized_symbol
    ON actions_log (normalized_symbol);

CREATE INDEX IF NOT EXISTS ix_actions_log_run_date
    ON actions_log (run_date DESC);
