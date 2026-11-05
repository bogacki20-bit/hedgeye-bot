-- 128: mid-week ETF Pro changes (Kris 2026-11-05). "UPDATE: N New ETF Pro Changes" emails name the
-- adds and removes but carry no table, so they never reached hedgeye_etf_pro_ranges. This keeps
-- every change; parser_etf_pro also applies them to the latest week's rows.
CREATE TABLE IF NOT EXISTS hedgeye_etf_pro_changes (
    message_id   TEXT NOT NULL,
    signal_date  DATE NOT NULL,
    ticker       TEXT NOT NULL,
    action       TEXT NOT NULL,
    bias         TEXT NOT NULL,
    range_low    NUMERIC,
    range_high   NUMERIC,
    parsed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (message_id, ticker, action)
);
CREATE INDEX IF NOT EXISTS idx_etf_pro_changes_date ON hedgeye_etf_pro_changes(signal_date DESC);
