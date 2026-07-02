-- 045_book_tables.sql
--
-- Two tables for the "book" SCREEN source:
--   book_positions  -> current holdings snapshot (one row per symbol per account per snapshot_date)
--   book_activity   -> trade history (buys/sells/option opens+closes), personal spending EXCLUDED
--
-- Design notes:
--   * `underlying` is the join key to ticker_tags.ticker. For equities it equals the symbol;
--     for options it is the OCC-parsed root (-AMZN260717P230 -> AMZN).
--   * Python owns ALL arithmetic. These tables store what the parser computed; the DB does no math.
--   * Snapshots are additive: each import writes a new snapshot_date so you keep history
--     (raw material for later per-quad book behaviour, mirrors the enroll-never-remove discipline).

BEGIN;

CREATE TABLE IF NOT EXISTS book_positions (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE        NOT NULL,
    account_number  TEXT        NOT NULL,
    account_name    TEXT        NOT NULL,
    symbol          TEXT        NOT NULL,   -- raw Fidelity symbol, e.g. 'AGGH' or '-AMZN260717P230'
    underlying      TEXT        NOT NULL,   -- join key to ticker_tags.ticker
    description     TEXT,
    asset_class     TEXT        NOT NULL,   -- 'equity' | 'option' | 'cash'
    is_option       BOOLEAN     NOT NULL DEFAULT FALSE,
    opt_expiry      DATE,
    opt_type        TEXT,                   -- 'C' | 'P' | NULL
    opt_strike      NUMERIC,
    quantity        NUMERIC     NOT NULL,
    last_price      NUMERIC,
    market_value    NUMERIC,                -- Fidelity "Current Value"
    cost_basis      NUMERIC,
    avg_cost        NUMERIC,
    total_gl_dollar NUMERIC,
    total_gl_pct    NUMERIC,
    pct_of_account  NUMERIC,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_date, account_number, symbol)
);

CREATE INDEX IF NOT EXISTS ix_bookpos_underlying ON book_positions (underlying);
CREATE INDEX IF NOT EXISTS ix_bookpos_snapshot   ON book_positions (snapshot_date);
CREATE INDEX IF NOT EXISTS ix_bookpos_class      ON book_positions (asset_class);

CREATE TABLE IF NOT EXISTS book_activity (
    id              BIGSERIAL PRIMARY KEY,
    run_date        DATE        NOT NULL,
    settlement_date DATE,
    account_number  TEXT        NOT NULL,
    account_name    TEXT        NOT NULL,
    action_raw      TEXT        NOT NULL,   -- full Fidelity Action string, kept verbatim
    action_type     TEXT        NOT NULL,   -- 'buy'|'sell'|'option_open'|'option_close'|'income'|'reinvest'|'fee'|'cash_move'
    side            TEXT,                   -- 'buy'|'sell' where applicable
    symbol          TEXT,                   -- raw symbol (may be an option symbol)
    underlying      TEXT,                   -- join key to ticker_tags.ticker
    is_option       BOOLEAN     NOT NULL DEFAULT FALSE,
    opt_expiry      DATE,
    opt_type        TEXT,
    opt_strike      NUMERIC,
    description     TEXT,
    price           NUMERIC,
    quantity        NUMERIC,
    commission      NUMERIC,
    fees            NUMERIC,
    amount          NUMERIC,                -- signed cash impact
    row_hash        TEXT        NOT NULL,   -- dedupe key (run_date|account|action|symbol|amount|qty)
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (row_hash)
);

CREATE INDEX IF NOT EXISTS ix_bookact_underlying ON book_activity (underlying);
CREATE INDEX IF NOT EXISTS ix_bookact_rundate    ON book_activity (run_date);
CREATE INDEX IF NOT EXISTS ix_bookact_type       ON book_activity (action_type);

COMMIT;
