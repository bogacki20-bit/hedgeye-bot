-- 144: intraday Risk Range refreshes (capture desk 11/19/2026).
-- Keith refreshes ranges inside Real-Time Alerts ("SPX Refreshed Risk Range (RR) = 7152-7509"). hedgeye_risk_ranges
-- holds one morning row per (ticker, signal_date), so the refresh lands here and tools/level_lines.lookup() takes the
-- newest same-day refresh over the morning row. Written by parser_rta.store_refreshes().
BEGIN;
CREATE TABLE IF NOT EXISTS hedgeye_risk_range_refresh (
    ticker          TEXT NOT NULL,
    signal_date     DATE NOT NULL,          -- ET date of the alert
    refreshed_at    TIMESTAMPTZ NOT NULL,   -- email received_at
    range_low       NUMERIC(14,4) NOT NULL,
    range_high      NUMERIC(14,4) NOT NULL,
    snippet         TEXT,                   -- the sentence it came from
    source          TEXT NOT NULL DEFAULT 'rta_refresh',
    source_email_id TEXT NOT NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, source_email_id)
);
CREATE INDEX IF NOT EXISTS idx_rr_refresh_ticker_at ON hedgeye_risk_range_refresh (ticker, refreshed_at DESC);
COMMIT;
