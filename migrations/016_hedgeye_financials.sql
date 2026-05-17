-- ============================================================================
-- Migration 016 — typed table for the Financials Earnings Recap product
--
-- Subjects: "1Q26 Financial(s) Earnings Recap | XYZ, AFRM, FOUR"
-- Body per ticker:
--   "XYZ ( Best Idea Long ): <thesis title> (Quad 2) Summary: ... Recap: ..."
-- Ratings seen: Best Idea Long/Short, Long/Short Bench.
--
-- (The image-only "Position Monitors | Financials" feed is handled by the
-- multi-sector Position Monitors parser, not here.)
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_financials (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    ticker          TEXT NOT NULL,
    rating          TEXT,                        -- "Best Idea Long" / "Short Bench" / ...
    side            TEXT,                        -- long|short (derived from rating)
    thesis_title    TEXT,
    quad            TEXT,                        -- "Quad 2" when present
    product         TEXT NOT NULL DEFAULT 'earnings_recap',
    feed_item_id    BIGINT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, ticker, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_fin_date   ON hedgeye_financials(signal_date);
CREATE INDEX IF NOT EXISTS idx_fin_ticker ON hedgeye_financials(ticker);
CREATE INDEX IF NOT EXISTS idx_fin_rating ON hedgeye_financials(rating);
