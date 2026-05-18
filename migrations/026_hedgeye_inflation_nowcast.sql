-- ============================================================================
-- Migration 026 — typed table for "Hedgeye Monthly Inflation Nowcast"
--
-- Real macro signal (feeds Quad detection): the body states the reported
-- headline CPI y/y + sequential bps for the just-released month, and the
-- forward base-case nowcast (y/y + sequential bps) for the next month,
-- plus the next CPI release date and an accelerating/decelerating regime.
--
-- This SUPERSEDES the metadata-only research_notes capture for this
-- product (those rows are deleted; emails reclassified inflation_nowcast).
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_inflation_nowcast (
    id                 BIGSERIAL PRIMARY KEY,
    signal_date        DATE NOT NULL,            -- email received date
    report_period      TEXT,                     -- month the printed CPI refers to ("April")
    headline_cpi_yoy   NUMERIC(6,2),             -- reported headline CPI % y/y
    headline_seq_bps   INT,                      -- sequential change, bps (+ accel / - decel)
    nowcast_period     TEXT,                     -- month the forward nowcast targets ("May")
    nowcast_yoy        NUMERIC(6,2),             -- base-case nowcast % y/y
    nowcast_seq_bps    INT,                      -- nowcast sequential change, bps
    cpi_release_date   DATE,                     -- next CPI release date
    regime             TEXT,                     -- accelerating | decelerating | mixed
    quad               TEXT,                     -- "Quad 2" when present
    feed_item_id       BIGINT,
    source_email_id    TEXT REFERENCES hedgeye_emails_raw(message_id)
                            ON DELETE SET NULL,
    parsed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_infn_date   ON hedgeye_inflation_nowcast(signal_date);
CREATE INDEX IF NOT EXISTS idx_infn_regime ON hedgeye_inflation_nowcast(regime);

-- This product now has a dedicated typed table; drop the metadata-only
-- research_notes rows so there is a single source of truth.
DELETE FROM hedgeye_research_notes WHERE product = 'inflation_nowcast';
