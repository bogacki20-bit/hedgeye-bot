-- ============================================================================
-- Migration 018 — publication log for the Position Monitors / Founder's
-- Choice sector feeds.
--
-- Subjects:
--   "Position Monitors | <Sector> - Best Idea Longs & Shorts"
--   "Founder's Choice: <Sector> - Best Idea Longs & Shorts"
--
-- These emails are IMAGE-ONLY — the long/short tables ship as graphics
-- ("BEST IDEAS - LONGS ( VIEW LARGER IMAGE )"), so no ticker text is
-- extractable. This table is therefore a publication LOG (which sector's
-- monitor was published when), satisfying full typed-table coverage even
-- though it carries no ticker-level signal. ML consumers should treat
-- has_image_payload=TRUE rows as "monitor refreshed, see image", not as
-- ticker signals.
--
-- (Single-name "Add/Remove ... (TICKER)" model-portfolio changes are NOT
-- here — they share the Investing Ideas body grammar and are routed to
-- hedgeye_ii_changes.)
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_position_monitors (
    id                 BIGSERIAL PRIMARY KEY,
    signal_date        DATE NOT NULL,
    sector             TEXT NOT NULL,
    product            TEXT NOT NULL,            -- position_monitor | founders_choice
    has_image_payload  BOOLEAN NOT NULL DEFAULT TRUE,
    feed_item_id       BIGINT,
    source_email_id    TEXT REFERENCES hedgeye_emails_raw(message_id)
                            ON DELETE SET NULL,
    parsed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, sector, product, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_pm_date    ON hedgeye_position_monitors(signal_date);
CREATE INDEX IF NOT EXISTS idx_pm_sector  ON hedgeye_position_monitors(sector);
CREATE INDEX IF NOT EXISTS idx_pm_product ON hedgeye_position_monitors(product);
