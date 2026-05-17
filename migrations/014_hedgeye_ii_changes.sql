-- ============================================================================
-- Migration 014 — typed table for Investing Ideas roster CHANGES
--
-- Distinct from hedgeye_investing_ideas (the Top-22 Leaderboard snapshot).
-- These are discrete add/remove events. Subjects:
--   "4 Changes: Remove CZR, Add ROKU, CARR, SXT"
--   "Top Stock Picks | REMOVE: EFX; ADD: SYF"
-- Body (authoritative):
--   "We are ADDING the following to Investing Ideas : Long: ROKU CARR SXT
--    We have REMOVED the following from Investing Ideas : Long: CZR (27.01 - 29.1)"
--
-- One email yields N rows (one per ticker changed). change_type is part of the
-- key so an add+remove of the same symbol in one email both persist.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_ii_changes (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    ticker          TEXT NOT NULL,
    change_type     TEXT NOT NULL,               -- 'add' | 'remove'
    side            TEXT,                        -- 'long' | 'short'
    price_low       NUMERIC(14,4),               -- remove stop-out range low
    price_high      NUMERIC(14,4),               -- remove stop-out range high
    reason          TEXT,                        -- e.g. "stopped out on #VASP"
    feed_item_id    BIGINT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, ticker, change_type, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_iic_date   ON hedgeye_ii_changes(signal_date);
CREATE INDEX IF NOT EXISTS idx_iic_ticker ON hedgeye_ii_changes(ticker);
CREATE INDEX IF NOT EXISTS idx_iic_type   ON hedgeye_ii_changes(change_type);
