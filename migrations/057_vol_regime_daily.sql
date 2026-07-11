-- 057_vol_regime_daily.sql — the second regime axis (build queue item 1).
--
-- One row per vol index per date, computed by Python (tools/vol_regime.py)
-- from that date's own mfr_snapshots rows. Rows are daily FACTS — written
-- once, never updated — so any corpus table can stamp vol regime by joining
-- on date. Backfillable honestly for every date the vol complex has MFR data.
--
-- phase: width (range_high-range_low, % of price) vs 5 trading days earlier:
--   compressing  = vol coming out (width shrank >10%)
--   widening     = vol building   (width grew  >10%)
--   steady       = within ±10%

BEGIN;

CREATE TABLE IF NOT EXISTS vol_regime_daily (
    id          BIGSERIAL PRIMARY KEY,
    as_of       DATE NOT NULL,
    index_name  TEXT NOT NULL,          -- VIX / VXN / RVX / MOVE / OVX / GVZ
    sleeve      TEXT NOT NULL,          -- equity / tech / smallcap / rates / oil / gold
    price       NUMERIC,
    trend       TEXT,                   -- BULLISH = vol rising regime
    momentum    TEXT,
    range_pos   NUMERIC,                -- position in MFR risk range 0..1
    width_pct   NUMERIC,                -- (high-low)/price
    width_chg5d NUMERIC,                -- width_pct / width_pct(5td ago) - 1
    phase       TEXT,                   -- compressing / widening / steady / n-a
    UNIQUE (as_of, index_name)
);

CREATE INDEX IF NOT EXISTS ix_volregime_date ON vol_regime_daily (as_of);

COMMIT;
