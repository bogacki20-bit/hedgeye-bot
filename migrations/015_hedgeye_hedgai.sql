-- ============================================================================
-- Migration 015 — typed table for HedgAI Signals (algorithmic multi-asset)
--
-- Body has a fixed 4-section block:
--   "Buys Added: <assets>  Shorts Added: <assets>
--    Buys Closed: <assets>  Shorts Closed: <assets>  ( VIEW LARGER IMAGE )"
-- Assets are comma-separated free-text names, often with a trailing ETF
-- ticker ("Energy XLE", "High Yield Corp Bond HYG"); many have no ticker
-- ("Steel", "Shanghai Composite", "S&P 500"), so asset_name is the key.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_hedgai (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    signal_ts       TIMESTAMPTZ,
    asset_name      TEXT NOT NULL,               -- "Energy XLE" / "Steel"
    ticker          TEXT,                        -- trailing ETF ticker, when present
    action          TEXT NOT NULL,               -- buy_add|short_add|buy_close|short_close
    side            TEXT,                        -- long|short
    is_close        BOOLEAN NOT NULL DEFAULT FALSE,
    feed_item_id    BIGINT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, asset_name, action, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_hedgai_date   ON hedgeye_hedgai(signal_date);
CREATE INDEX IF NOT EXISTS idx_hedgai_ticker ON hedgeye_hedgai(ticker);
CREATE INDEX IF NOT EXISTS idx_hedgai_action ON hedgeye_hedgai(action);
