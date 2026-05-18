-- ============================================================================
-- Migration 027 — typed table for "Investing Ideas Newsletter" (weekly)
--
-- Distinct from hedgeye_investing_ideas (Top-22 Leaderboard) and
-- hedgeye_ii_changes (add/remove events). The Newsletter is the weekly
-- full roster + price levels:
--   "Long: DAR, SWBI, MUSA ...  Short: ROP, RBLX, ONON ...
--    LEVELS Longs STOCK PREV.CLOSE ... DAR $63.12 $60.02 $65.91 ..."
-- One email -> N (ticker, side) rows with prev_close + trend range.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_ii_newsletter (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,               -- long|short
    prev_close      NUMERIC(14,4),
    range_low       NUMERIC(14,4),
    range_high      NUMERIC(14,4),
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    feed_item_id    BIGINT,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, ticker, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_iin_date   ON hedgeye_ii_newsletter(signal_date);
CREATE INDEX IF NOT EXISTS idx_iin_ticker ON hedgeye_ii_newsletter(ticker);
