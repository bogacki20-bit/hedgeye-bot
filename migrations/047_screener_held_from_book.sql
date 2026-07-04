-- 047_screener_held_from_book.sql — SCREEN "in my book" reads book_positions.
--
-- The held flag (📗own / "in my book" filter) now comes from the Fidelity book
-- (book_positions, migration 045) instead of the older positions_snapshot. Keyed
-- on `underlying` so an option on a name flags the underlying as held too. Cash
-- sweeps (asset_class='cash') and zero-quantity rows are excluded. Everything else
-- in v_screener is unchanged from 044.

DROP VIEW IF EXISTS v_screener;

CREATE VIEW v_screener AS
WITH latest_mfr AS (
    SELECT DISTINCT ON (ticker) ticker, snapshot_date, price, range_low, range_high,
           trend_signal, momentum_signal, hurst, hurst_3mo,
           iv, rv, (full_payload->>'ivpd')::numeric AS ivpd
    FROM mfr_snapshots
    ORDER BY ticker, snapshot_date DESC
),
hedgeye_trend AS (
    SELECT DISTINCT ON (ticker) ticker, trend
    FROM hedgeye_risk_ranges
    ORDER BY ticker, signal_date DESC
),
held AS (
    SELECT DISTINCT underlying AS ticker
    FROM book_positions
    WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
      AND asset_class <> 'cash'
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
    lm.hurst, lm.hurst_3mo, lm.iv, lm.rv, lm.ivpd,
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
