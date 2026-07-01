-- 041_screener.sql — SCREEN command backing objects.
--
-- ticker_tags: the operator's tagged universe (433 names, loaded from
-- ticker_tags.csv; DDL here for reproducibility — data is loaded separately).
-- v_screener: one row per tagged ticker joining the latest MFR snapshot
-- (range/price/momentum) and the latest Hedgeye risk-range TREND, plus a held
-- flag from the newest portfolio positions snapshot. All screening math lives
-- in the view / Python (tools/screener.py) — no LLM.

CREATE TABLE IF NOT EXISTS ticker_tags (
    ticker              TEXT PRIMARY KEY,
    hedgeye_group       TEXT,
    hedgeye_bucket_0629 TEXT,
    gics_sector         TEXT,
    subsector           TEXT,
    cyclicality         TEXT,
    rate_sensitive      SMALLINT,
    commodity_linked    SMALLINT,
    duration_char       TEXT,
    review              SMALLINT
);

CREATE OR REPLACE VIEW v_screener AS
WITH latest_mfr AS (
    SELECT DISTINCT ON (ticker) ticker, snapshot_date, price, range_low, range_high
    FROM mfr_snapshots
    ORDER BY ticker, snapshot_date DESC
),
ranked AS (
    SELECT ticker, price,
           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
    FROM mfr_snapshots
),
price_20d AS (                       -- price ~20 trading sessions back
    SELECT ticker, price AS price_20d_ago FROM ranked WHERE rn = 21
),
latest_trend AS (
    SELECT DISTINCT ON (ticker) ticker, trend AS trend_dir
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
    lt.trend_dir,
    (h.ticker IS NOT NULL) AS held,
    (lm.range_low IS NOT NULL) AS has_range
FROM ticker_tags tt
LEFT JOIN latest_mfr   lm  ON lm.ticker  = tt.ticker
LEFT JOIN price_20d    p20 ON p20.ticker = tt.ticker
LEFT JOIN latest_trend lt  ON lt.ticker  = tt.ticker
LEFT JOIN held         h   ON h.ticker   = tt.ticker;
