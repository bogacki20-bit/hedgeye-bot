-- ============================================================================
-- Migration 021 — typed table for "The Call @ Hedgeye" (+ "The Call Recap")
--
-- Body carries a structured HEDGEYE POSITIONS block:
--   "HEDGEYE POSITIONS ... LONGS: CZR, VIK, MGM, RCL ... SHORTS: ..."
-- One email -> N (ticker, side) rows.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_the_call (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,               -- long|short
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    feed_item_id    BIGINT,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, ticker, side, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_call_date   ON hedgeye_the_call(signal_date);
CREATE INDEX IF NOT EXISTS idx_call_ticker ON hedgeye_the_call(ticker);
