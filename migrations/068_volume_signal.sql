-- ============================================================================
-- Migration 068 — volume signal (2026-07-14)
--
-- Keith's entry rule, stated in every alert: "buying dips on DECELERATING
-- volume." The bot had no volume series to test it against. This table holds
-- the per-ticker daily volume read.
--
--   volume_snapshots — one row per (ticker, snapshot_date):
--     vol_vs_20d   = latest volume / trailing-20-session average volume.
--                    >1 = above-average participation; <1 = quiet / fading.
--     vol_slope_3d = normalized slope of volume across the last 3 DOWN days
--                    (close < prior close). < 0 = volume decelerating into
--                    weakness (seller exhaustion → real dip). > 0 with price
--                    down = distribution (same rp, opposite trade).
--     decelerating = vol_slope_3d < 0 (volume fading on the down days).
--     price_down_3d = net 3-session return < 0 (context for the slope).
--     A "real dip" (buyable weakness) = price_down_3d AND decelerating.
--
-- Pairs with the RS grid: PASS_THE_PUCK + decelerating dip = high conviction.
-- Source is the daily OHLCV feed (yfinance now; volume rides along with the
-- close). mfr_snapshots.previous_day_volume is the ongoing cross-check.
--
-- Apply via:  py apply_migration.py migrations/068_volume_signal.sql
-- Idempotent (CREATE TABLE IF NOT EXISTS) — additive only.
-- ============================================================================

CREATE TABLE IF NOT EXISTS volume_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    snapshot_date  DATE NOT NULL,
    ticker         TEXT NOT NULL,

    volume         BIGINT,          -- latest session volume
    avg_vol_20d    BIGINT,          -- trailing 20-session average
    vol_vs_20d     NUMERIC(10,4),   -- volume / avg_vol_20d
    vol_slope_3d   NUMERIC(12,8),   -- normalized slope over last 3 down days
    down_days_used INTEGER,         -- how many down days the slope used (<=3)

    decelerating   BOOLEAN,         -- vol_slope_3d < 0
    price_down_3d  BOOLEAN,         -- net 3-session return < 0
    real_dip       BOOLEAN,         -- price_down_3d AND decelerating (buyable)

    n_obs          INTEGER,         -- volume observations available
    source         TEXT NOT NULL DEFAULT 'yfinance',
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_vol_snap_date
    ON volume_snapshots (snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_vol_snap_ticker
    ON volume_snapshots (ticker, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_vol_snap_realdip
    ON volume_snapshots (snapshot_date, real_dip);
