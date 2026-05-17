-- ============================================================================
-- Migration 019 — typed table for MOMO Tracker (Christian Drake)
--
-- MOMO Tracker bodies are IMAGE-ONLY ("( VIEW LARGER IMAGE )" x N); ALL
-- structured signal is encoded in the SUBJECT line, e.g.
--   "MOMO Tracker | Mag7 (+0.5%), MSFT/ORCL=BULLISH, TSLA (+4%, BULLISH),
--    ORCL To Bullish Trend"
-- So this parser is subject-driven: per-ticker % move and/or bullish/
-- bearish/neutral sentiment. "Mag7" is kept as a pseudo-ticker MAG7.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_momo (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    ticker          TEXT NOT NULL,               -- incl. pseudo "MAG7"
    pct_change      NUMERIC(8,2),
    sentiment       TEXT,                        -- bullish|bearish|neutral
    raw_token       TEXT,                        -- subject fragment it came from
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, ticker, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_momo_date   ON hedgeye_momo(signal_date);
CREATE INDEX IF NOT EXISTS idx_momo_ticker ON hedgeye_momo(ticker);
