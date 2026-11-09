-- 129: Hedgeye's country quad table from /macro_dashboard (Kris 2026-11-05), one row per
-- country + quarter per read, so moves in the forward path can be seen between reads.
CREATE TABLE IF NOT EXISTS hedgeye_country_quads (
    read_date  DATE NOT NULL,
    country    TEXT NOT NULL,
    period     TEXT NOT NULL,
    quad       SMALLINT NOT NULL CHECK (quad BETWEEN 1 AND 4),
    read_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (read_date, country, period)
);
CREATE INDEX IF NOT EXISTS idx_country_quads_country ON hedgeye_country_quads(country, read_date DESC);
