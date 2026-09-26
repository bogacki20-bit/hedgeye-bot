-- 116: TradingView-read TRADE/TREND levels (Kris 9/26: the bot's Alpha Code blend).
--   TREND = MFR Trend (Myfractalrange Trend) daily band
--   TRADE = Vola2vola volatility Trade & Trend daily line
-- Neither is in MFR's API; both are read overnight from TradingView in Kris's
-- Chrome (tools/tv_levels.py + scheduled task tv-levels-overnight).
-- One row per (ticker, bar_date) so history accrues. Reversible: DROP TABLE tv_levels.
CREATE TABLE IF NOT EXISTS tv_levels (
    ticker           TEXT NOT NULL,       -- bot ticker (v_screener)
    bar_date         DATE NOT NULL,       -- the completed daily bar the values are from
    tv_symbol        TEXT,
    mfr_trend_upper  NUMERIC,
    mfr_trend_lower  NUMERIC,
    vola_a           NUMERIC,             -- Vola2vola plot 1 (step line)
    vola_b           NUMERIC,             -- Vola2vola plot 3 (curve)
    bar_close        NUMERIC,
    captured_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_date)
);
CREATE INDEX IF NOT EXISTS tv_levels_latest ON tv_levels (ticker, bar_date DESC);

-- Failures (symbol didn't resolve / indicators produced no data) so coverage is visible.
CREATE TABLE IF NOT EXISTS tv_levels_misses (
    ticker      TEXT NOT NULL,
    run_date    DATE NOT NULL,
    tv_symbol   TEXT,
    reason      TEXT,
    PRIMARY KEY (ticker, run_date)
);
