-- ============================================================================
-- Migration 126: research_digest + research_calls (Kris 11/2/2026)
--
-- hedgeye_emails_raw keeps every email verbatim and hedgeye_research_notes
-- keeps product/title/tickers, but nothing keeps what a report actually says.
-- research_digest = one row per report: summary, regime read, process read,
-- data series. research_calls = one row per name/theme the report touches,
-- so "what has research said about OMF?" is one query.
-- All statements idempotent.
-- ============================================================================

CREATE TABLE IF NOT EXISTS research_digest (
    id            BIGSERIAL PRIMARY KEY,
    message_id    TEXT NOT NULL UNIQUE REFERENCES hedgeye_emails_raw(message_id)
                       ON DELETE CASCADE,
    signal_date   DATE NOT NULL,
    product       TEXT NOT NULL,
    title         TEXT,
    summary       TEXT NOT NULL,
    regime_read   TEXT,
    process_read  TEXT,
    data          JSONB NOT NULL DEFAULT '{}',
    extractor     TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rd_date    ON research_digest(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_rd_product ON research_digest(product);

CREATE TABLE IF NOT EXISTS research_calls (
    id            BIGSERIAL PRIMARY KEY,
    digest_id     BIGINT NOT NULL REFERENCES research_digest(id) ON DELETE CASCADE,
    signal_date   DATE NOT NULL,
    product       TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('ticker','theme')),
    name          TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN
                    ('long','short','bullish','bearish','mixed','watch')),
    basis         TEXT NOT NULL CHECK (basis IN ('stated','inferred')),
    thesis        TEXT NOT NULL,
    UNIQUE (digest_id, kind, name)
);
CREATE INDEX IF NOT EXISTS idx_rc_name ON research_calls(name, signal_date DESC);
