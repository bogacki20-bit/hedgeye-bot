-- ============================================================================
-- Migration 023 — typed table for "Keith's Signal Longs/Shorts"
--
-- Body: "Keith's Signal Strength List  LONGS: V, XYZ, AFRM, CFG ...
--         SHORTS : MA, ADYEY, FISV ..."  -> N (ticker, side) rows.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_keiths_signals (
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

CREATE INDEX IF NOT EXISTS idx_ks_date   ON hedgeye_keiths_signals(signal_date);
CREATE INDEX IF NOT EXISTS idx_ks_ticker ON hedgeye_keiths_signals(ticker);
