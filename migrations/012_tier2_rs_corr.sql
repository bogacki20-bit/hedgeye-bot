-- Migration 012: TIER 2 – price_history + correlation_snapshots method column
-- Shadow mode only. Apply before running tools/price_backfill.py.

-- ── price_history ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS price_history (
    id         BIGSERIAL PRIMARY KEY,
    ticker     TEXT        NOT NULL,
    d          DATE        NOT NULL,
    close      NUMERIC     NOT NULL,
    source     TEXT        NOT NULL DEFAULT 'yfinance',
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, d)
);

CREATE INDEX IF NOT EXISTS idx_price_history_ticker_d
    ON price_history (ticker, d);

-- ── correlation_snapshots: add method + widen uniqueness ──────────────────────
-- method names the math so a formula change forces new rows, not silent updates
ALTER TABLE correlation_snapshots
    ADD COLUMN IF NOT EXISTS method TEXT NOT NULL DEFAULT 'pearson_logret_v1';

-- n_obs: how many overlapping bars went into the number
ALTER TABLE correlation_snapshots
    ADD COLUMN IF NOT EXISTS n_obs INTEGER;

-- Drop the old unique constraint and rebuild wider to include method
DO $$
DECLARE
    _con TEXT;
BEGIN
    SELECT conname INTO _con
    FROM   pg_constraint
    WHERE  conrelid = 'correlation_snapshots'::regclass
    AND    contype  = 'u'
    LIMIT  1;
    IF _con IS NOT NULL THEN
        EXECUTE 'ALTER TABLE correlation_snapshots DROP CONSTRAINT ' || quote_ident(_con);
    END IF;
END $$;

ALTER TABLE correlation_snapshots
    ADD CONSTRAINT uq_corr_snap
    UNIQUE (ticker_a, ticker_b, window_days, snapshot_date, method);
