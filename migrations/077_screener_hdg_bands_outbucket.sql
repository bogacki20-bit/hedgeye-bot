-- 077_screener_hdg_bands_outbucket.sql — two read-path bugs, one view.
--
-- (1) TREND: hedgeye_trend was "latest hedgeye_risk_ranges row, forever".
--     When Keith parks a name in the #OutBucket it stops appearing in the
--     main Risk Range table, so its last main-table row froze and was served
--     as current indefinitely — XLU showed BULLISH from 7/30 while the
--     OutBucket had it BEARISH since 7/31, and the short was flagged
--     trend-against. The OutBucket IS parsed (parser_risk_range.py →
--     hedgeye_signal_changes, change_type in ('out_bucket','trend_change'))
--     but no trend consumer read it. Resolution: the authoritative Hedgeye
--     trend is the NEWEST by signal_date across the active table and the
--     change feed. No staleness cutoff on trend — OutBucket tags are live
--     until Hedgeye changes them (they republish daily as trend_change rows).
--
-- (2) BANDS: range_low/range_high/range_pos came only from mfr_snapshots.
--     The source hierarchy says hdg overwrites mfr, and the ingest was doing
--     its half (hedgeye_risk_ranges.buy_trade/sell_trade, upserted daily) —
--     but the read never looked there, so the report displayed mfr bands on
--     names with fresh Hedgeye levels. Resolution: a FRESH hdg band
--     (signal_date within 7 calendar days ≈ 5 sessions, matching
--     RR_MAX_AGE_DAYS in db_pg) overrides the mfr band; stale hdg bands are
--     ignored so a name that left the rotation falls back to mfr instead of
--     freezing (the same failure trend had). band_source says which won.
--     range_pos numerator stays lm.price (MFR latest price) — the freshest
--     price the view holds; hdg prev_close is the prior close at publication.
--
-- has_range keeps its original meaning — "MFR coverage exists" — because the
-- DARK/enrollment surfaces use it to find MFR enrollment gaps.

DROP VIEW IF EXISTS v_screener;

CREATE VIEW v_screener AS
WITH latest_mfr AS (
    SELECT DISTINCT ON (ticker) ticker, snapshot_date, price, range_low, range_high,
           trend_signal, momentum_signal, hurst, hurst_3mo,
           iv, rv, (full_payload->>'ivpd')::numeric AS ivpd
    FROM mfr_snapshots
    ORDER BY ticker, snapshot_date DESC
),
hedgeye_rr AS (
    SELECT DISTINCT ON (ticker) ticker, trend, buy_trade, sell_trade, signal_date
    FROM hedgeye_risk_ranges
    ORDER BY ticker, signal_date DESC
),
hedgeye_chg AS (
    SELECT DISTINCT ON (ticker) ticker, new_state AS trend, signal_date
    FROM hedgeye_signal_changes
    WHERE change_type IN ('out_bucket', 'trend_change')
      AND new_state IN ('BULLISH', 'BEARISH', 'NEUTRAL')
    ORDER BY ticker, signal_date DESC, parsed_at DESC
),
hedgeye_trend AS (
    SELECT ticker,
           CASE WHEN ch.signal_date IS NOT NULL
                 AND (rr.signal_date IS NULL OR ch.signal_date >= rr.signal_date)
                THEN ch.trend ELSE rr.trend END AS trend
    FROM hedgeye_rr rr
    FULL OUTER JOIN hedgeye_chg ch USING (ticker)
),
hdg_band AS (
    SELECT ticker, buy_trade AS range_low, sell_trade AS range_high, signal_date
    FROM hedgeye_rr
    WHERE buy_trade IS NOT NULL AND sell_trade IS NOT NULL
      AND sell_trade > buy_trade
      AND signal_date >= CURRENT_DATE - 7
),
held AS (
    SELECT DISTINCT underlying AS ticker
    FROM book_positions
    WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
      AND asset_class <> 'cash'
      AND COALESCE(quantity, 0) <> 0
),
universe AS (
    SELECT ticker FROM ticker_tags
    UNION
    SELECT ticker FROM held
)
SELECT
    u.ticker, tt.gics_sector, tt.subsector, tt.hedgeye_bucket_0629, tt.hedgeye_group,
    lm.snapshot_date, lm.price,
    COALESCE(hb.range_low,  lm.range_low)  AS range_low,
    COALESCE(hb.range_high, lm.range_high) AS range_high,
    (lm.price - COALESCE(hb.range_low, lm.range_low))
      / NULLIF(COALESCE(hb.range_high, lm.range_high)
               - COALESCE(hb.range_low, lm.range_low), 0) AS range_pos,
    CASE WHEN hb.ticker IS NOT NULL THEN 'hdg'
         WHEN lm.range_low IS NOT NULL THEN 'mfr' END AS band_source,
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
FROM universe u
LEFT JOIN ticker_tags    tt ON tt.ticker = u.ticker
LEFT JOIN latest_mfr     lm ON lm.ticker = u.ticker
LEFT JOIN hedgeye_trend  ht ON ht.ticker = u.ticker
LEFT JOIN hdg_band       hb ON hb.ticker = u.ticker
LEFT JOIN held           h  ON h.ticker  = u.ticker;
