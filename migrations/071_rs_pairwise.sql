CREATE TABLE IF NOT EXISTS rs_pairwise (
    id BIGSERIAL PRIMARY KEY,
    as_of DATE NOT NULL,
    base TEXT NOT NULL,
    vs   TEXT NOT NULL,
    rs_trend    NUMERIC(10,6),
    rs_delta_3d NUMERIC(10,6),
    n_obs INTEGER,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (as_of, base, vs)
);
CREATE INDEX IF NOT EXISTS ix_rspair_date ON rs_pairwise (as_of DESC);
