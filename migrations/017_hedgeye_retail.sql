-- ============================================================================
-- Migration 017 — typed table for the Retail product family (Brian McGough)
--
-- Three structured text feeds (the image-only "Position Monitors | Retail"
-- feed is handled by the multi-sector Position Monitors parser):
--   soundbites   : "Actionable Retail Soundbites and Daily Callouts M/D"
--                   body "Today's Callouts Include: AMZN, BOOT, ..."
--   key_callouts : "KEY RETAIL CALLOUTS FOR THE WEEK | M/D"
--                   body "WRBY / REAL / ..." + LONG/SHORT COVERAGE sections
--   sunday_edge  : "Sunday Retail EDGE | Going Active Long PLNT, ATZ, VVV"
--
-- One email yields N ticker rows. side is best-effort (LONG/SHORT COVERAGE
-- sections, subject Long/Short keyword); NULL when the feed is just callouts.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_retail (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    ticker          TEXT NOT NULL,
    product         TEXT NOT NULL,               -- soundbites|key_callouts|sunday_edge
    side            TEXT,                        -- long|short|NULL
    feed_item_id    BIGINT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, ticker, product, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_ret_date    ON hedgeye_retail(signal_date);
CREATE INDEX IF NOT EXISTS idx_ret_ticker  ON hedgeye_retail(ticker);
CREATE INDEX IF NOT EXISTS idx_ret_product ON hedgeye_retail(product);
