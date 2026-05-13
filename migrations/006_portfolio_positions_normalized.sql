-- ============================================================================
-- 006 — portfolio_positions: add normalized_symbol + natural-key UNIQUE so
--       repeated CSV ingests upsert in place rather than appending duplicates.
-- ============================================================================

ALTER TABLE portfolio_positions
    ADD COLUMN IF NOT EXISTS normalized_symbol TEXT;

-- The pre-existing constraint (snapshot_date, account_number, symbol) does
-- not account for Cash+Margin sleeves of the same ticker (Fidelity emits two
-- rows). Drop it in favor of the new index below that includes account_type.
ALTER TABLE portfolio_positions
    DROP CONSTRAINT IF EXISTS portfolio_positions_snapshot_date_account_number_symbol_key;

-- Natural key: same account, same instrument, same account_type (Cash/Margin)
-- on the same snapshot_date. NULLs coalesced so the index is reusable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_positions_natural
    ON portfolio_positions (
        account_number,
        symbol,
        COALESCE(account_type, ''),
        snapshot_date
    );

CREATE INDEX IF NOT EXISTS ix_portfolio_positions_normalized_symbol
    ON portfolio_positions (normalized_symbol);
