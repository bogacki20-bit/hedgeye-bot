-- 042_screener_mfr_trend_fallback.sql — add MFR trend fallback to v_screener.
--
-- Hedgeye risk-range TREND covers only ~10 of the tagged universe, so the
-- mandatory TREND gate emptied almost every screen. trend_dir now COALESCEs the
-- Hedgeye TREND (primary) with MFR's trend_signal (fallback), lifting coverage
-- to ~400/433. A trend_source column ('hedgeye'/'mfr') records which fired.
-- DROP+CREATE because the new trend_source column can't be inserted mid-list via
-- CREATE OR REPLACE.

DROP VIEW IF EXISTS v_screener;

CREATE VIEW v_screener AS
WITH latest_mfr AS (
    SELECT DISTINCT ON (ticker) ticker, snapshot_date, price, range_low, range_high, trend_signal
    FROM mfr_snapshots
    ORDER BY ticker, snapshot_date DESC
),
ranked AS (
    SELECT ticker, price,
           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
    FROM mfr_snapshots
),
price_20d AS (
    SELECT ticker, price AS price_20d_ago FROM ranked WHERE rn = 21
),
hedgeye_trend AS (
    SELECT DISTINCT ON (ticker) ticker, trend
    FROM hedgeye_risk_ranges
    ORDER BY ticker, signal_date DESC
),
held AS (
    SELECT DISTINCT normalized_symbol AS ticker
    FROM positions_snapshot
    WHERE snapshot_date = (SELECT max(snapshot_date) FROM positions_snapshot)
      AND COALESCE(quantity, 0) <> 0
)
SELECT
    tt.ticker, tt.gics_sector, tt.subsector, tt.hedgeye_bucket_0629, tt.hedgeye_group,
    lm.snapshot_date, lm.price, lm.range_low, lm.range_high,
    (lm.price - lm.range_low) / NULLIF(lm.range_high - lm.range_low, 0) AS range_pos,
    p20.price_20d_ago,
    CASE WHEN lm.price IS NOT NULL AND p20.price_20d_ago IS NOT NULL
         THEN (lm.price > p20.price_20d_ago) END AS momentum_ok,
    -- Hedgeye TREND primary; MFR trend_signal fallback.
    COALESCE(ht.trend, CASE lm.trend_signal
         WHEN 'trendBullish' THEN 'BULLISH'
         WHEN 'trendBearish' THEN 'BEARISH'
         WHEN 'trendNeutral' THEN 'NEUTRAL' END) AS trend_dir,
    CASE WHEN ht.trend IS NOT NULL THEN 'hedgeye'
         WHEN lm.trend_signal IN ('trendBullish','trendBearish','trendNeutral') THEN 'mfr' END AS trend_source,
    (h.ticker IS NOT NULL) AS held,
    (lm.range_low IS NOT NULL) AS has_range
FROM ticker_tags tt
LEFT JOIN latest_mfr    lm  ON lm.ticker  = tt.ticker
LEFT JOIN price_20d     p20 ON p20.ticker = tt.ticker
LEFT JOIN hedgeye_trend ht  ON ht.ticker  = tt.ticker
LEFT JOIN held          h   ON h.ticker   = tt.ticker;
