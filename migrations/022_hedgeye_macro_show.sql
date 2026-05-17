-- ============================================================================
-- Migration 022 — typed table for THE MACRO SHOW (Keith McCullough daily)
--
-- Summary Notes editions carry a structured block:
--   "TL;DR - POSITIONS MENTIONED — BULLISH: Brent Oil (BNO), Nvidia (NVDA),
--    Amazon (AMZN) ... BEARISH: Long-Duration Treasuries (TLT) ..."
-- Tickers are in parens after the asset name. One email -> N (ticker,side)
-- rows; side derived from BULLISH/BEARISH. Access/Top-3 editions with no
-- positions block classify with no rows (still typed-covered).
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_macro_show (
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

CREATE INDEX IF NOT EXISTS idx_ms_date   ON hedgeye_macro_show(signal_date);
CREATE INDEX IF NOT EXISTS idx_ms_ticker ON hedgeye_macro_show(ticker);
