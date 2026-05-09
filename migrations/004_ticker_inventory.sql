-- ============================================================================
-- Migration 004 — Hedgeye ticker inventory + monitored ticker view
--
-- Purpose: maintain a running "universe of things Hedgeye has ever signaled
-- on" so we can answer questions like:
--   - What is Keith currently long under Quad 2?
--   - What tickers have we ever seen in any Hedgeye source?
--   - Which tickers have we never re-tested with MFR?
--
-- Also creates the monitored_tickers view that price_monitor.py uses to
-- decide what to actually fetch yfinance prices for, replacing the hardcoded
-- ALERT_TICKERS set.
--
-- Apply via apply_migration.py (uses DATABASE_PUBLIC_URL).
-- All statements idempotent.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_ticker_inventory (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL UNIQUE,

    -- When Hedgeye first/most-recently signaled on this ticker.
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Most recent directional bias from any Hedgeye product.
    -- 'long' / 'short' / 'neutral' / 'closed'
    current_position    TEXT,

    -- Most recent source that mentioned this ticker.
    -- 'risk_range' / 'rta' / 'investing_ideas' / 'macro_show' /
    -- 'signal_change' / 'sector_pro' / 'etf_pro' / 'other'
    last_source         TEXT,

    -- Snapshot of Quad regime when ticker FIRST appeared.
    quad_at_first_seen  TEXT,                  -- 'Quad 1' / 'Quad 2' / 'Quad 3' / 'Quad 4'

    -- Current Quad regime as of last update.
    quad_current        TEXT,

    -- Total times we've seen this ticker in a Hedgeye source. Auto-incremented.
    mention_count       INTEGER NOT NULL DEFAULT 0,

    -- When MFR was last refreshed for this ticker (NULL if never).
    last_mfr_fetched_at TIMESTAMPTZ,

    -- When yfinance price was last polled for this ticker.
    last_yf_polled_at   TIMESTAMPTZ,

    -- Whether the bot should actively watch this ticker (default true; can be
    -- toggled false to mute noisy/irrelevant tickers without removing history).
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,

    metadata            JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_inventory_ticker     ON hedgeye_ticker_inventory(ticker);
CREATE INDEX IF NOT EXISTS idx_inventory_last_seen  ON hedgeye_ticker_inventory(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_inventory_position   ON hedgeye_ticker_inventory(current_position);
CREATE INDEX IF NOT EXISTS idx_inventory_quad       ON hedgeye_ticker_inventory(quad_current);
CREATE INDEX IF NOT EXISTS idx_inventory_active     ON hedgeye_ticker_inventory(is_active) WHERE is_active = TRUE;


-- ============================================================================
-- Inventory history — every time a ticker is seen, append a row here.
-- Lets us reconstruct "in Quad 3 we held X long, then Quad rotated and we
-- went short" without losing the audit trail.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_ticker_history (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL,
    seen_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    source              TEXT NOT NULL,
    position            TEXT,                            -- long/short/neutral/closed
    quad_regime         TEXT,                            -- Quad at time of mention
    price_at_mention    NUMERIC(14,4),

    -- Reference to the email or signal that produced this mention, if known.
    message_id          TEXT,                            -- IMAP message-id
    signal_email_id     BIGINT,                          -- hedgeye_emails_raw.id

    metadata            JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_tickerhist_ticker  ON hedgeye_ticker_history(ticker, seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_tickerhist_source  ON hedgeye_ticker_history(source);
CREATE INDEX IF NOT EXISTS idx_tickerhist_quad    ON hedgeye_ticker_history(quad_regime);


-- ============================================================================
-- monitored_tickers VIEW — what price_monitor.py polls.
-- Combines tickers from active Risk Ranges + the inventory table's is_active
-- flag, so anything Hedgeye is currently signaling on automatically becomes
-- a price-monitor target.
-- ============================================================================

CREATE OR REPLACE VIEW monitored_tickers AS
SELECT DISTINCT
    COALESCE(rr.ticker, inv.ticker) AS ticker,
    inv.current_position,
    inv.quad_current,
    inv.last_source,
    inv.last_seen_at
FROM hedgeye_ticker_inventory inv
FULL OUTER JOIN (
    -- Most recent Risk Range row per ticker, within last 14 days
    SELECT DISTINCT ON (ticker) ticker, signal_date
    FROM hedgeye_risk_ranges
    WHERE signal_date >= CURRENT_DATE - INTERVAL '14 days'
    ORDER BY ticker, signal_date DESC
) rr ON inv.ticker = rr.ticker
WHERE COALESCE(inv.is_active, TRUE) = TRUE;
