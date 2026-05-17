-- ============================================================================
-- Migration 020 — typed table for CRYPTO QUANT (Christian Drake / FIG)
--
-- CRYPTO QUANT bodies are IMAGE-ONLY; structured signal is in the SUBJECT:
--   "CRYPTO QUANT | ₿TC (-0.9%), ETH/SOL/AVAX/XRP=BEARISH, XRP=BULLISH"
-- Subject-driven: per-asset % move and/or bullish/bearish/neutral.
-- "₿TC" (and its cp1252-mangled "?TC") normalises to BTC.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_crypto_quant (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    asset           TEXT NOT NULL,               -- BTC/ETH/SOL/XRP/COIN/...
    pct_change      NUMERIC(8,2),
    sentiment       TEXT,                        -- bullish|bearish|neutral
    raw_token       TEXT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, asset, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_cq_date  ON hedgeye_crypto_quant(signal_date);
CREATE INDEX IF NOT EXISTS idx_cq_asset ON hedgeye_crypto_quant(asset);
