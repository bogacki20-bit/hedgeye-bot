-- 103: 7-day staleness gate on the Hedgeye TREND in v_screener.
-- Ranges already had this window (hdg_band); trends did not, so a dead
-- row could outrank fresh MFR forever. Same definition as 102 otherwise.

CREATE OR REPLACE VIEW v_screener AS
WITH latest_mfr AS (
         SELECT DISTINCT ON (mfr_snapshots.ticker) mfr_snapshots.ticker,
            mfr_snapshots.snapshot_date,
            mfr_snapshots.price,
            mfr_snapshots.range_low,
            mfr_snapshots.range_high,
            mfr_snapshots.trend_signal,
            mfr_snapshots.momentum_signal,
            mfr_snapshots.hurst,
            mfr_snapshots.hurst_3mo,
            mfr_snapshots.iv,
            mfr_snapshots.rv,
            (mfr_snapshots.full_payload ->> 'ivpd'::text)::numeric AS ivpd
           FROM mfr_snapshots
          ORDER BY mfr_snapshots.ticker, mfr_snapshots.snapshot_date DESC
        ), hedgeye_rr AS (
         SELECT DISTINCT ON (hedgeye_risk_ranges.ticker) hedgeye_risk_ranges.ticker,
            hedgeye_risk_ranges.trend,
            hedgeye_risk_ranges.buy_trade,
            hedgeye_risk_ranges.sell_trade,
            hedgeye_risk_ranges.signal_date
           FROM hedgeye_risk_ranges
          ORDER BY hedgeye_risk_ranges.ticker, hedgeye_risk_ranges.signal_date DESC
        ), hedgeye_chg AS (
         SELECT DISTINCT ON (hedgeye_signal_changes.ticker) hedgeye_signal_changes.ticker,
            hedgeye_signal_changes.new_state AS trend,
            hedgeye_signal_changes.signal_date
           FROM hedgeye_signal_changes
          WHERE (hedgeye_signal_changes.change_type = ANY (ARRAY['out_bucket'::text, 'trend_change'::text]))
            AND (hedgeye_signal_changes.new_state = ANY (ARRAY['BULLISH'::text, 'BEARISH'::text, 'NEUTRAL'::text]))
          ORDER BY hedgeye_signal_changes.ticker, hedgeye_signal_changes.signal_date DESC, hedgeye_signal_changes.parsed_at DESC
        ), hedgeye_trend AS (
         -- STALENESS GATE (migration 103): a Hedgeye trend older than 7 days
         -- is NOT a live fact — the name left the rotation (XLI: a May-21
         -- BULLISH overrode fresh MFR BEARISH for months). NULL here makes
         -- trend_dir AND trend_source fall through to MFR, mirroring the
         -- 7-day window hdg_band already applies to ranges.
         SELECT ticker,
                CASE
                    WHEN ch.signal_date IS NOT NULL AND ch.signal_date >= (CURRENT_DATE - 7)
                         AND (rr.signal_date IS NULL OR ch.signal_date >= rr.signal_date) THEN ch.trend
                    WHEN rr.signal_date IS NOT NULL AND rr.signal_date >= (CURRENT_DATE - 7) THEN rr.trend
                    ELSE NULL
                END AS trend
           FROM hedgeye_rr rr
             FULL JOIN hedgeye_chg ch USING (ticker)
        ), hdg_band AS (
         SELECT hedgeye_rr.ticker,
            hedgeye_rr.buy_trade AS range_low,
            hedgeye_rr.sell_trade AS range_high,
            hedgeye_rr.signal_date
           FROM hedgeye_rr
          WHERE hedgeye_rr.buy_trade IS NOT NULL AND hedgeye_rr.sell_trade IS NOT NULL
            AND hedgeye_rr.sell_trade > hedgeye_rr.buy_trade
            AND hedgeye_rr.signal_date >= (CURRENT_DATE - 7)
        ), held AS (
         SELECT DISTINCT v_book_effective.underlying AS ticker
           FROM v_book_effective
          WHERE v_book_effective.asset_class <> 'cash'::text
            AND COALESCE(v_book_effective.quantity, 0::numeric) <> 0::numeric
        ), universe AS (
         SELECT ticker_tags.ticker
           FROM ticker_tags
        UNION
         SELECT held.ticker
           FROM held
        )
 SELECT u.ticker,
    tt.gics_sector,
    tt.subsector,
    tt.hedgeye_bucket_0629,
    tt.hedgeye_group,
    lm.snapshot_date,
    lm.price,
    COALESCE(hb.range_low, lm.range_low) AS range_low,
    COALESCE(hb.range_high, lm.range_high) AS range_high,
    (lm.price - COALESCE(hb.range_low, lm.range_low)) / NULLIF(COALESCE(hb.range_high, lm.range_high) - COALESCE(hb.range_low, lm.range_low), 0::numeric) AS range_pos,
        CASE
            WHEN hb.ticker IS NOT NULL THEN 'hdg'::text
            WHEN lm.range_low IS NOT NULL THEN 'mfr'::text
            ELSE NULL::text
        END AS band_source,
    lm.momentum_signal,
        CASE lm.momentum_signal
            WHEN 'momentumBullish'::text THEN 'BULLISH'::text
            WHEN 'momentumBearish'::text THEN 'BEARISH'::text
            WHEN 'momentumNeutral'::text THEN 'NEUTRAL'::text
            WHEN 'momentumNeutralDanger'::text THEN 'NEUTRAL'::text
            ELSE NULL::text
        END AS momentum_dir,
    lm.momentum_signal = 'momentumBullish'::text AS momentum_ok,
        CASE lm.trend_signal
            WHEN 'trendBullish'::text THEN 'BULLISH'::text
            WHEN 'trendBearish'::text THEN 'BEARISH'::text
            WHEN 'trendNeutral'::text THEN 'NEUTRAL'::text
            ELSE NULL::text
        END AS mfr_trade_dir,
        CASE
            WHEN lm.trend_signal = 'trendBullish'::text AND lm.momentum_signal = 'momentumBearish'::text THEN 'bull-trade/bear-mom'::text
            WHEN lm.trend_signal = 'trendBearish'::text AND lm.momentum_signal = 'momentumBullish'::text THEN 'bear-trade/bull-mom'::text
            ELSE NULL::text
        END AS divergence,
    lm.hurst,
    lm.hurst_3mo,
    lm.iv,
    lm.rv,
    lm.ivpd,
    COALESCE(ht.trend,
        CASE lm.trend_signal
            WHEN 'trendBullish'::text THEN 'BULLISH'::text
            WHEN 'trendBearish'::text THEN 'BEARISH'::text
            WHEN 'trendNeutral'::text THEN 'NEUTRAL'::text
            ELSE NULL::text
        END) AS trend_dir,
        CASE
            WHEN ht.trend IS NOT NULL THEN 'hedgeye'::text
            WHEN lm.trend_signal = ANY (ARRAY['trendBullish'::text, 'trendBearish'::text, 'trendNeutral'::text]) THEN 'mfr'::text
            ELSE NULL::text
        END AS trend_source,
    h.ticker IS NOT NULL AS held,
    lm.range_low IS NOT NULL AS has_range
   FROM universe u
     LEFT JOIN ticker_tags tt ON tt.ticker = u.ticker
     LEFT JOIN latest_mfr lm ON lm.ticker = u.ticker
     LEFT JOIN hedgeye_trend ht ON ht.ticker = u.ticker
     LEFT JOIN hdg_band hb ON hb.ticker = u.ticker
     LEFT JOIN held h ON h.ticker = u.ticker;
