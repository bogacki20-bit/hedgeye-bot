-- 118: live_quotes.fetched_at = the QUOTE's market timestamp (last 1-minute bar);
-- polled_at = when we asked Yahoo. v_screener compares the quote's own time
-- against MFR's fetch time, so an after-hours poll of Friday's close still
-- dates to Friday 15:59, not to the poll. Reversible: DROP COLUMN polled_at.
ALTER TABLE live_quotes ADD COLUMN IF NOT EXISTS polled_at TIMESTAMPTZ;
ALTER TABLE quote_snapshots ADD COLUMN IF NOT EXISTS quote_at TIMESTAMPTZ;
