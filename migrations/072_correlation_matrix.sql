CREATE TABLE IF NOT EXISTS correlation_matrix (
    ticker_a    TEXT NOT NULL,
    ticker_b    TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    correlation NUMERIC(8,5),
    n_obs       INTEGER,
    as_of       DATE,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker_a, ticker_b, window_days)
);
CREATE INDEX IF NOT EXISTS ix_corrmatrix_a ON correlation_matrix (ticker_a, window_days);
CREATE INDEX IF NOT EXISTS ix_corrmatrix_b ON correlation_matrix (ticker_b, window_days);
