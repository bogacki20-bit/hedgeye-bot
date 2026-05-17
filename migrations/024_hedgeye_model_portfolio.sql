-- ============================================================================
-- Migration 024 — publication log for "Model Portfolio Changes"
--
-- The actual changes ship as a PDF / image ("PDF HERE . ( VIEW LARGER
-- IMAGE )"); the email text only states the effective date
-- ("...go into effect using closing prices on Monday, May 11, 2026").
-- So this is a publication-log row per email with the effective date —
-- typed-table coverage without inventing ticker rows that aren't in text.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_model_portfolio (
    id                BIGSERIAL PRIMARY KEY,
    signal_date       DATE NOT NULL,             -- email received date
    effective_date    DATE,                      -- when the changes take effect
    has_pdf_payload   BOOLEAN NOT NULL DEFAULT TRUE,
    feed_item_id      BIGINT,
    source_email_id   TEXT REFERENCES hedgeye_emails_raw(message_id)
                            ON DELETE SET NULL,
    parsed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_mp_date ON hedgeye_model_portfolio(signal_date);
CREATE INDEX IF NOT EXISTS idx_mp_eff  ON hedgeye_model_portfolio(effective_date);
