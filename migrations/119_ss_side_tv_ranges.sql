-- 119: Signal Strength goes SIDED + TradingView ranges as the last-resort range tier.
-- Kris 9/29: the SS dashboard (new layout, CSV export) lists 53 longs AND 96 shorts;
-- the "Signal Strength Stocks" email only carries the long table, so the bot's roster
-- had been long-only with no side column. Existing rows are the longs.
-- TradingView's "MFR Range" indicator reproduces MFR's st/lt ranges (9/26 test:
-- equities 119/120 same zone), so names MFR doesn't cover get a range from it —
-- only when hdg / iin / mfr all have none (band_source/price_source = 'tv').

ALTER TABLE ss_roster_history ADD COLUMN IF NOT EXISTS side TEXT NOT NULL DEFAULT 'long';
DO $$ BEGIN
  ALTER TABLE ss_roster_history ADD CONSTRAINT ss_roster_history_side_chk CHECK (side IN ('long','short'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS ss_roster_history_open_side ON ss_roster_history (side) WHERE removed_on IS NULL;
ALTER TABLE ss_roster_anchor ADD COLUMN IF NOT EXISTS side TEXT;

CREATE OR REPLACE VIEW ss_roster_current AS
    SELECT ticker, added_on, add_source, side
      FROM ss_roster_history
     WHERE removed_on IS NULL;

ALTER TABLE tv_levels ADD COLUMN IF NOT EXISTS range_high NUMERIC;
ALTER TABLE tv_levels ADD COLUMN IF NOT EXISTS range_low NUMERIC;
ALTER TABLE tv_levels ADD COLUMN IF NOT EXISTS lt_range_high NUMERIC;
ALTER TABLE tv_levels ADD COLUMN IF NOT EXISTS lt_range_low NUMERIC;

CREATE OR REPLACE VIEW v_screener AS
 SELECT b.ticker,
    b.gics_sector,
    b.subsector,
    b.hedgeye_bucket_0629,
    b.hedgeye_group,
    b.snapshot_date,
    b.price,
    b.range_low,
    b.range_high,
    b.range_pos,
    b.band_source,
    b.momentum_signal,
    b.momentum_dir,
    b.momentum_ok,
    b.mfr_trade_dir,
    b.divergence,
    b.hurst,
    b.hurst_3mo,
    b.iv,
    b.rv,
    b.ivpd,
    b.trend_dir,
    b.trend_source,
    b.held,
    b.has_range,
    b.price_source,
    b.price_at,
    b.mfr_price,
        CASE
            WHEN b.trend_dir ~~ 'BEAR%'::text THEN tv.mfr_trend_upper
            ELSE tv.mfr_trend_lower
        END AS tv_trend,
        CASE
            WHEN b.trend_dir ~~ 'BEAR%'::text THEN LEAST(tv.vola_a, tv.vola_b)
            ELSE GREATEST(tv.vola_a, tv.vola_b)
        END AS tv_trade,
        CASE
            WHEN tv.ticker IS NULL THEN NULL::text
            WHEN b.trend_dir ~~ 'BEAR%'::text THEN 'bear-mirror'::text
            ELSE 'bull'::text
        END AS tv_basis,
    tv.bar_date AS tv_bar_date,
    tv.mfr_trend_upper AS tv_mfr_upper,
    tv.mfr_trend_lower AS tv_mfr_lower,
    tv.vola_a AS tv_vola_a,
    tv.vola_b AS tv_vola_b
   FROM ( WITH latest_mfr AS (
                 SELECT DISTINCT ON (mfr_snapshots.ticker) mfr_snapshots.ticker,
                    mfr_snapshots.snapshot_date,
                    mfr_snapshots.price,
                    mfr_snapshots.fetched_at AS mfr_fetched_at,
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
                  WHERE (hedgeye_signal_changes.change_type = ANY (ARRAY['out_bucket'::text, 'trend_change'::text])) AND (hedgeye_signal_changes.new_state = ANY (ARRAY['BULLISH'::text, 'BEARISH'::text, 'NEUTRAL'::text]))
                  ORDER BY hedgeye_signal_changes.ticker, hedgeye_signal_changes.signal_date DESC, hedgeye_signal_changes.parsed_at DESC
                ), hedgeye_trend AS (
                 SELECT ticker,
                        CASE
                            WHEN ch.signal_date IS NOT NULL AND ch.signal_date >= (CURRENT_DATE - 7) AND (rr.signal_date IS NULL OR ch.signal_date >= rr.signal_date) THEN ch.trend
                            WHEN rr.signal_date IS NOT NULL AND rr.signal_date >= (CURRENT_DATE - 7) THEN rr.trend
                            ELSE NULL::text
                        END AS trend
                   FROM hedgeye_rr rr
                     FULL JOIN hedgeye_chg ch USING (ticker)
                ), hdg_band AS (
                 SELECT hedgeye_rr.ticker,
                    hedgeye_rr.buy_trade AS range_low,
                    hedgeye_rr.sell_trade AS range_high,
                    hedgeye_rr.signal_date
                   FROM hedgeye_rr
                  WHERE hedgeye_rr.buy_trade IS NOT NULL AND hedgeye_rr.sell_trade IS NOT NULL AND hedgeye_rr.sell_trade > hedgeye_rr.buy_trade AND hedgeye_rr.signal_date >= (CURRENT_DATE - 7)
                ), iin_band AS (
                 SELECT DISTINCT ON (n.ticker) n.ticker,
                    n.range_low,
                    n.range_high,
                    n.signal_date
                   FROM hedgeye_ii_newsletter n
                  WHERE n.range_low IS NOT NULL AND n.range_high > n.range_low AND n.signal_date >= (CURRENT_DATE - 7)
                  ORDER BY n.ticker, n.signal_date DESC
                ), held AS (
                 SELECT DISTINCT v_book_effective.underlying AS ticker
                   FROM v_book_effective
                  WHERE v_book_effective.asset_class <> 'cash'::text AND COALESCE(v_book_effective.quantity, 0::numeric) <> 0::numeric
                ), universe AS (
                 SELECT ticker_tags.ticker
                   FROM ticker_tags
                UNION
                 SELECT held.ticker
                   FROM held
                UNION
                 SELECT hedgeye_etf_pro_ranges.ticker
                   FROM hedgeye_etf_pro_ranges
                  WHERE hedgeye_etf_pro_ranges.week_of >= (CURRENT_DATE - 14)
                UNION
                 SELECT ss_roster_current.ticker
                   FROM ss_roster_current
                UNION
                 SELECT hedgeye_sector_monitor_events.ticker
                   FROM hedgeye_sector_monitor_events
                  WHERE hedgeye_sector_monitor_events.signal_date >= (CURRENT_DATE - 90)
                UNION
                 SELECT short_shelf.ticker
                   FROM short_shelf
                  WHERE short_shelf.status = 'live'::text
                ), live_px AS (
                 SELECT live_quotes.ticker,
                    live_quotes.price AS lq_price,
                    live_quotes.fetched_at AS lq_at
                   FROM live_quotes
                ), tv_rng AS (
                 SELECT DISTINCT ON (tv_levels.ticker) tv_levels.ticker,
                    tv_levels.range_high::numeric(14,4) AS range_high,
                    tv_levels.range_low::numeric(14,4) AS range_low,
                    tv_levels.bar_close::numeric(14,4) AS bar_close
                   FROM tv_levels
                  WHERE tv_levels.range_high IS NOT NULL AND tv_levels.range_high > tv_levels.range_low
                    AND tv_levels.bar_date >= (CURRENT_DATE - 10)
                  ORDER BY tv_levels.ticker, tv_levels.bar_date DESC
                )
         SELECT u.ticker,
            tt.gics_sector,
            tt.subsector,
            tt.hedgeye_bucket_0629,
            tt.hedgeye_group,
            lm.snapshot_date,
                CASE
                    WHEN lq.lq_price IS NOT NULL AND (lm.mfr_fetched_at IS NULL OR lq.lq_at >= lm.mfr_fetched_at) THEN lq.lq_price
                    ELSE COALESCE(lm.price, tr.bar_close)
                END::numeric(14,4) AS price,
            COALESCE(hb.range_low, ib.range_low, lm.range_low, tr.range_low) AS range_low,
            COALESCE(hb.range_high, ib.range_high, lm.range_high, tr.range_high) AS range_high,
            (
                CASE
                    WHEN lq.lq_price IS NOT NULL AND (lm.mfr_fetched_at IS NULL OR lq.lq_at >= lm.mfr_fetched_at) THEN lq.lq_price
                    ELSE COALESCE(lm.price, tr.bar_close)
                END - COALESCE(hb.range_low, ib.range_low, lm.range_low, tr.range_low)) / NULLIF(COALESCE(hb.range_high, ib.range_high, lm.range_high, tr.range_high) - COALESCE(hb.range_low, ib.range_low, lm.range_low, tr.range_low), 0::numeric) AS range_pos,
                CASE
                    WHEN hb.ticker IS NOT NULL THEN 'hdg'::text
                    WHEN ib.ticker IS NOT NULL THEN 'iin'::text
                    WHEN lm.range_low IS NOT NULL THEN 'mfr'::text
                    WHEN tr.range_low IS NOT NULL THEN 'tv'::text
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
            lm.range_low IS NOT NULL AS has_range,
                CASE
                    WHEN lq.lq_price IS NOT NULL AND (lm.mfr_fetched_at IS NULL OR lq.lq_at >= lm.mfr_fetched_at) THEN 'yahoo'::text
                    WHEN lm.price IS NOT NULL THEN 'mfr'::text
                    WHEN tr.bar_close IS NOT NULL THEN 'tv'::text
                    ELSE NULL::text
                END AS price_source,
                CASE
                    WHEN lq.lq_price IS NOT NULL AND (lm.mfr_fetched_at IS NULL OR lq.lq_at >= lm.mfr_fetched_at) THEN lq.lq_at
                    ELSE lm.mfr_fetched_at
                END AS price_at,
            lm.price AS mfr_price
           FROM universe u
             LEFT JOIN ticker_tags tt ON tt.ticker = u.ticker
             LEFT JOIN latest_mfr lm ON lm.ticker = u.ticker
             LEFT JOIN hedgeye_trend ht ON ht.ticker = u.ticker
             LEFT JOIN hdg_band hb ON hb.ticker = u.ticker
             LEFT JOIN iin_band ib ON ib.ticker = u.ticker
             LEFT JOIN held h ON h.ticker = u.ticker
             LEFT JOIN live_px lq ON lq.ticker = u.ticker
             LEFT JOIN tv_rng tr ON tr.ticker = u.ticker) b
     LEFT JOIN LATERAL ( SELECT l.ticker,
            l.bar_date,
            l.mfr_trend_upper,
            l.mfr_trend_lower,
            l.vola_a,
            l.vola_b
           FROM tv_levels l
          WHERE l.ticker = b.ticker
          ORDER BY l.bar_date DESC
         LIMIT 1) tv ON true;
