-- 137: MFR platform indicators, one row per ticker per daily bar (Kris 2026-11-15).
-- Source: MyFractalRange MCP (mfr_get_indicator), read by tools/mfr_indicators.py with MFR_MCP_TOKEN.
-- Core group (vola_*, trend_*, tm_/tq_/ty_*) = the card's lines: Trade = vola_trade,
-- Trend = the MFR Trend band (trend_bear = bottom for longs, trend_bull = top for shorts).
-- Track group (bs_*, btfd_buy, mom_*, vix*, mx_*) is scored before any process use.
BEGIN;
CREATE TABLE IF NOT EXISTS mfr_indicators (
    ticker          text        NOT NULL,
    bar_date        date        NOT NULL,
    -- VOLA_TT: Vola2vola Trade & Trend on price; bear = 1 while volatility is stressed (red)
    vola_trade      numeric,
    vola_trend      numeric,
    vola_bear       smallint,
    -- MFR_TREND: the yellow cloud. bull = upper band, bear = lower band
    trend_bull      numeric,
    trend_bear      numeric,
    -- MFR_TARGETS_M/Q/A: period open (pivot) and two targets each way
    tm_open numeric, tm_up1 numeric, tm_up2 numeric, tm_dn1 numeric, tm_dn2 numeric,
    tq_open numeric, tq_up1 numeric, tq_up2 numeric, tq_dn1 numeric, tq_dn2 numeric,
    ty_open numeric, ty_up1 numeric, ty_up2 numeric, ty_dn1 numeric, ty_dn2 numeric,
    -- BS_SIGNALS / BTFD flags (1 on a signal bar)
    bs_buy smallint, bs_mega_buy smallint, bs_sell smallint, bs_mega_sell smallint,
    btfd_buy smallint,
    -- MOMENTUM: momentum relative to volatility, short / medium / long
    mom_p1 numeric, mom_p2 numeric, mom_p3 numeric,
    -- VOLA gauge pane: VIX Fix with its own trade / trend lines
    vixfix numeric, vix_trade numeric, vix_trend numeric, vix_trend_down smallint, vix_hi numeric, vix_lo numeric,
    -- MATRIX pane
    mx_wvf numeric, mx_wvf_hot smallint, mx_open numeric, mx_close numeric, mx_bull smallint,
    mx_res numeric, mx_sup numeric, mx_up_x numeric, mx_down_x numeric,
    core_fetched_at  timestamptz,
    track_fetched_at timestamptz,
    PRIMARY KEY (ticker, bar_date)
);
CREATE INDEX IF NOT EXISTS mfr_indicators_date_idx ON mfr_indicators (bar_date);

-- Latest bar per ticker, for desk lookups.
CREATE OR REPLACE VIEW v_mfr_indicators_latest AS
SELECT DISTINCT ON (ticker) *
FROM mfr_indicators
ORDER BY ticker, bar_date DESC;
COMMIT;
