-- ============================================================================
-- Migration 025 — hedgeye_research_notes: consolidated typed table for the
-- narrative/macro research family (one row per email, product discriminator).
--
-- These products are prose / data commentary, not ticker rosters: Early
-- Look, Market Situation Report, Monthly Inflation Nowcast, Fed H.8,
-- Macro Week Summary Notes, JT Taylor Geopolitical, Quads/GIP Update,
-- Hedgeye QIO, Monthly Commentary, Senior Loan Officer Survey, Weekly
-- Position Monitor, Updated Levels, Trend & Tail Levels, KM Top 3 Things.
--
-- Captured: product, title (subject), quad regime, author handles,
-- parenthetical tickers mentioned, body length, feed id. This gives the
-- ML set full coverage of every signal source Keith publishes while being
-- honest that these carry context, not a tradeable ticker roster.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_research_notes (
    id                BIGSERIAL PRIMARY KEY,
    signal_date       DATE NOT NULL,
    product           TEXT NOT NULL,             -- early_look | market_situation_report | ...
    title             TEXT,                      -- subject line
    quad              TEXT,                      -- "Quad 2" when present
    authors           TEXT,                      -- csv of @handles
    tickers_mentioned TEXT,                      -- csv of parenthetical tickers
    body_chars        INT,
    feed_item_id      BIGINT,
    source_email_id   TEXT REFERENCES hedgeye_emails_raw(message_id)
                            ON DELETE SET NULL,
    parsed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_rn_date    ON hedgeye_research_notes(signal_date);
CREATE INDEX IF NOT EXISTS idx_rn_product ON hedgeye_research_notes(product);
CREATE INDEX IF NOT EXISTS idx_rn_quad    ON hedgeye_research_notes(quad);
