-- 085_tv_history.sql — TradingView MFR indicator history (8-year backfill)
--
-- Two tables, deliberately SEPARATE from the live feed (mfr_snapshots stays
-- the live feed's alone; trendspider_export.py unions the two at read time):
--
--   tv_mfr_history      one row per (ticker, TV daily bar) from the exported
--                       "Export chart data" CSVs in data/tradingview/.
--                       known_at = bar_date 09:30 ET: Step 0 (2026-09-06)
--                       verified the feed publishes each bar's values the
--                       PRIOR evening (~20:12 ET) — weekend-dated live rows
--                       equal the NEXT TV bar, pre-open fetches match — so
--                       09:30 is conservative.
--   tv_features_history bar-derived features recomputed from the same TV
--                       bars with the bot's own live functions
--                       (shadow_range.hurst_rs, tools.relative_strength
--                       pearson/returns). known_at = bar_date 09:30 ET for
--                       range-derived features (rp / lt_rp / bull_dist —
--                       inputs are the prior close and the pre-published
--                       range) and bar_date 16:00 ET for close-window
--                       features (shadow_hurst, correlations — they consume
--                       bar D's own close).
--
-- trend_tag: derived by rule R3, validated in Step 0 at 353/354 across five
-- tickers: tag for bar D = sign of close[D-1] vs bull/bear levels[D-1]
-- (+1 above bull, -1 below bear, else 0). NULL on the first post-warm-up
-- bar (no prior levels).
--
-- Apply via:  py apply_migration.py migrations/085_tv_history.sql

CREATE TABLE IF NOT EXISTS tv_mfr_history (
    ticker         text        NOT NULL,
    bar_date       date        NOT NULL,
    known_at       timestamptz NOT NULL,
    range_high     numeric     NOT NULL,
    range_low      numeric     NOT NULL,
    lt_range_high  numeric     NOT NULL,
    lt_range_low   numeric     NOT NULL,
    bull_level     numeric     NOT NULL,
    bear_level     numeric     NOT NULL,
    trend_tag      integer,
    source_file    text        NOT NULL,
    ingested_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_date),
    CONSTRAINT tv_mfr_range_order CHECK (range_high > range_low),
    CONSTRAINT tv_mfr_lt_order    CHECK (lt_range_high > lt_range_low),
    CONSTRAINT tv_mfr_bull_bear   CHECK (bull_level > bear_level),
    CONSTRAINT tv_mfr_trend_tag   CHECK (trend_tag IN (-1, 0, 1))
);

CREATE INDEX IF NOT EXISTS idx_tv_mfr_history_date
    ON tv_mfr_history (bar_date);

CREATE TABLE IF NOT EXISTS tv_features_history (
    ticker       text        NOT NULL,
    bar_date     date        NOT NULL,
    known_at     timestamptz NOT NULL,
    feature      text        NOT NULL,
    value        numeric     NOT NULL,
    computed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_date, feature)
);

CREATE INDEX IF NOT EXISTS idx_tv_features_history_feature
    ON tv_features_history (feature, bar_date);

COMMENT ON TABLE tv_mfr_history IS
    'MFR indicator daily history from TradingView chart exports '
    '(data/tradingview/*.csv). Historical import only - the live feed stays '
    'in mfr_snapshots; trendspider_export.py unions the two.';
COMMENT ON TABLE tv_features_history IS
    'Bar-derived features recomputed from TV daily bars with the bot''s live '
    'functions (shadow hurst, range positions, return correlations). '
    'known_at: 09:30 ET bar date for range-derived, 16:00 ET for '
    'close-window features.';
