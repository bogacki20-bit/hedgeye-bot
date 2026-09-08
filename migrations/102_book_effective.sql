-- 102: same-day fill overlay (2026-09-08 operator report: Friday 9/4
-- fills invisible to BOOK; decision layer re-recommended trades already
-- executed). book_positions snapshots are pre-market downloads, so every
-- fill made during the session is invisible until the NEXT morning's CSV.
-- v_book_effective = latest snapshot + post-snapshot book_activity fills:
--   * snapshot_downloaded_at (new column, parsed from the CSV footer's
--     "Date downloaded" line) decides whether same-DATE fills are inside
--     the snapshot (post-close download) or must overlay (pre-market /
--     unknown, the 5 AM convention);
--   * equity rows repriced at snapshot last_price; brand-new names carry
--     fill cost until the next snapshot prices them;
--   * one synthetic '_FILL_CASH_ADJ' cash row per traded account keeps
--     account totals conserved (buys drain cash, sells refill it);
--   * options fills are NOT overlaid (no options by account rule; an
--     option fill row is a broker anomaly, not a position).
-- Readers (position_targets, book_direction, portfolio helpers,
-- v_screener's held CTE) move to this view — one book of record, now
-- fill-current.
ALTER TABLE book_positions
    ADD COLUMN IF NOT EXISTS snapshot_downloaded_at timestamptz;

CREATE OR REPLACE VIEW v_book_effective AS
WITH snap AS (
    SELECT max(snapshot_date) AS d FROM book_positions
), premkt AS (
    SELECT (max(b.snapshot_downloaded_at) IS NULL
            OR (max(b.snapshot_downloaded_at)
                AT TIME ZONE 'America/New_York')::time < TIME '09:30')
           AS pre
    FROM book_positions b, snap WHERE b.snapshot_date = snap.d
), f AS (
    SELECT a.account_number, a.symbol,
           sum(a.quantity) AS dq,
           sum(a.amount)   AS cash_flow,
           sum(CASE WHEN a.quantity > 0 THEN -a.amount ELSE 0 END) AS buy_cost,
           count(*)        AS n_fills,
           max(a.run_date) AS last_fill
    FROM book_activity a, snap, premkt
    WHERE NOT COALESCE(a.is_option, false)
      AND a.action_type IN ('buy', 'sell')
      AND (a.run_date > snap.d OR (a.run_date = snap.d AND premkt.pre))
    GROUP BY a.account_number, a.symbol
), p AS (
    SELECT b.* FROM book_positions b, snap WHERE b.snapshot_date = snap.d
), merged AS (
    SELECT (SELECT d FROM snap)                        AS snapshot_date,
           COALESCE(p.account_number, f.account_number) AS account_number,
           p.account_name,
           COALESCE(p.symbol, f.symbol)                AS symbol,
           COALESCE(p.underlying, f.symbol)            AS underlying,
           p.description,
           COALESCE(p.asset_class, 'equity')           AS asset_class,
           COALESCE(p.is_option, false)                AS is_option,
           p.opt_expiry, p.opt_type, p.opt_strike,
           CASE WHEN COALESCE(p.asset_class, '') = 'cash' THEN p.quantity
                ELSE COALESCE(p.quantity, 0) + COALESCE(f.dq, 0) END
                                                       AS quantity,
           p.last_price,
           CASE WHEN COALESCE(p.asset_class, '') = 'cash'
                  THEN p.market_value
                WHEN p.last_price IS NOT NULL
                  THEN (COALESCE(p.quantity, 0) + COALESCE(f.dq, 0))
                       * p.last_price
                WHEN f.buy_cost IS NOT NULL THEN f.buy_cost
                ELSE p.market_value END                AS market_value,
           p.cost_basis, p.avg_cost, p.total_gl_dollar, p.total_gl_pct,
           p.pct_of_account, p.lot_types,
           (f.symbol IS NOT NULL)                      AS overlaid,
           f.n_fills                                   AS overlay_fills,
           f.last_fill                                 AS overlay_through
    FROM p
    FULL OUTER JOIN f
      ON f.account_number = p.account_number AND f.symbol = p.symbol
     AND COALESCE(p.asset_class, '') <> 'cash'
)
SELECT * FROM merged
WHERE asset_class = 'cash' OR abs(COALESCE(quantity, 0)) > 1e-9
UNION ALL
SELECT (SELECT d FROM snap), f.account_number, NULL,
       '_FILL_CASH_ADJ', '_FILL_CASH_ADJ',
       'post-snapshot fill cash impact (overlay)', 'cash', false,
       NULL, NULL, NULL, NULL, NULL,
       sum(f.cash_flow), NULL, NULL, NULL, NULL, NULL, NULL,
       true, sum(f.n_fills)::bigint, max(f.last_fill)
FROM f
GROUP BY f.account_number
HAVING abs(sum(f.cash_flow)) > 0.005;

-- v_screener: only its `held` CTE touches the book — move it to the
-- effective view so SCREEN's held tag reflects today's fills too.
-- (Body otherwise identical to the live definition.)
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
         SELECT ticker,
                CASE
                    WHEN ch.signal_date IS NOT NULL AND (rr.signal_date IS NULL OR ch.signal_date >= rr.signal_date) THEN ch.trend
                    ELSE rr.trend
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
