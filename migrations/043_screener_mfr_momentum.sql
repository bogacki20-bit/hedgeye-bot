-- 043_screener_mfr_momentum.sql — momentum from MFR fields, not price history.
--
-- The old momentum_ok (price_today > price_20d_ago) needed ~20 daily snapshots and
-- read NULL on freshly-enrolled names. MFR already returns a momentum read, a
-- trade/trend read, and the Hurst exponent on every snapshot, so momentum is now
-- sourced from those (available the moment a name is enrolled):
--   momentum_ok  = momentum_signal is momentumBullish
--   momentum_dir = BULLISH/BEARISH/NEUTRAL from momentum_signal
--   mfr_trade_dir= BULLISH/BEARISH/NEUTRAL from trend_signal (MFR's native trade signal)
--   divergence   = MFR trade vs momentum directional-and-opposite (exhaustion-fade):
--                  'bull-trade/bear-mom' or 'bear-trade/bull-mom'
--   hurst,hurst_3mo exposed (>0.5 trending, <0.5 mean-reverting)
-- trend_dir/trend_source (the mandatory TREND gate: Hedgeye primary, MFR fallback)
-- are unchanged. price_20d_ago is dropped.

DROP VIEW IF EXISTS v_screener;

CREATE VIEW v_screener AS
WITH latest_mfr AS (
    SELECT DISTINCT ON (ticker) ticker, snapshot_date, price, range_low, range_high,
           trend_signal, momentum_signal, hurst, hurst_3mo
    FROM mfr_snapshots
    ORDER BY ticker, snapshot_date DESC
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
    lm.momentum_signal,
    CASE lm.momentum_signal
         WHEN 'momentumBullish' THEN 'BULLISH' WHEN 'momentumBearish' THEN 'BEARISH'
         WHEN 'momentumNeutral' THEN 'NEUTRAL' WHEN 'momentumNeutralDanger' THEN 'NEUTRAL' END AS momentum_dir,
    (lm.momentum_signal = 'momentumBullish') AS momentum_ok,
    CASE lm.trend_signal
         WHEN 'trendBullish' THEN 'BULLISH' WHEN 'trendBearish' THEN 'BEARISH'
         WHEN 'trendNeutral' THEN 'NEUTRAL' END AS mfr_trade_dir,
    CASE WHEN lm.trend_signal = 'trendBullish' AND lm.momentum_signal = 'momentumBearish' THEN 'bull-trade/bear-mom'
         WHEN lm.trend_signal = 'trendBearish' AND lm.momentum_signal = 'momentumBullish' THEN 'bear-trade/bull-mom' END AS divergence,
    lm.hurst, lm.hurst_3mo,
    COALESCE(ht.trend, CASE lm.trend_signal
         WHEN 'trendBullish' THEN 'BULLISH' WHEN 'trendBearish' THEN 'BEARISH'
         WHEN 'trendNeutral' THEN 'NEUTRAL' END) AS trend_dir,
    CASE WHEN ht.trend IS NOT NULL THEN 'hedgeye'
         WHEN lm.trend_signal IN ('trendBullish','trendBearish','trendNeutral') THEN 'mfr' END AS trend_source,
    (h.ticker IS NOT NULL) AS held,
    (lm.range_low IS NOT NULL) AS has_range
FROM ticker_tags tt
LEFT JOIN latest_mfr    lm ON lm.ticker = tt.ticker
LEFT JOIN hedgeye_trend ht ON ht.ticker = tt.ticker
LEFT JOIN held          h  ON h.ticker  = tt.ticker;
