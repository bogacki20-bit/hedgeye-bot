-- 100: hedgeye_trend_daily (roadmap §2 step 2c) — Keith's TREND tag
-- as-of each trading bar, forward-filled from hedgeye_risk_ranges over
-- the SPY calendar with a 5-trading-day staleness cap (beyond that the
-- instrument has rotated off his list and direction is NOT carried).
-- Built by ml/load_hedgeye_trend.py.
CREATE TABLE IF NOT EXISTS hedgeye_trend_daily (
    date             date NOT NULL,
    ticker           text NOT NULL,
    tag              text NOT NULL,
    last_signal_date date NOT NULL,
    staleness        int  NOT NULL,     -- trading days since the signal
    PRIMARY KEY (date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_htd_ticker ON hedgeye_trend_daily (ticker, date);
