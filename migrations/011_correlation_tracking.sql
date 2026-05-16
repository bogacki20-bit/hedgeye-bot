-- Migration 011 — correlation tracking + regime-shift detection.
--
-- NOTE: Task 12's detailed spec was truncated in the build prompt (the
-- slide images were pasted over the text right after "migrations/011_").
-- This schema is reconstructed from the task title "Correlation tracking
-- + regime shift detector" and the conventions of migrations 002-010.
-- Review before relying on it.
--
-- correlation_snapshots — rolling return-correlation per macro pair per
--   window, one row per (snapshot_date, pair, window).
-- regime_shifts — append-only log of detected correlation regime shifts
--   (sign flip or large magnitude change vs the prior snapshot).
--
-- Idempotent, additive only.

CREATE TABLE IF NOT EXISTS correlation_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    ticker_a      TEXT NOT NULL,
    ticker_b      TEXT NOT NULL,
    window_days   INTEGER NOT NULL,
    correlation   NUMERIC(8,5),
    n_obs         INTEGER,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, ticker_a, ticker_b, window_days)
);

CREATE INDEX IF NOT EXISTS idx_corr_pair
    ON correlation_snapshots (ticker_a, ticker_b, window_days, snapshot_date);

CREATE TABLE IF NOT EXISTS regime_shifts (
    id           BIGSERIAL PRIMARY KEY,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pair         TEXT NOT NULL,
    window_days  INTEGER NOT NULL,
    prev_corr    NUMERIC(8,5),
    curr_corr    NUMERIC(8,5),
    shift_type   TEXT NOT NULL,        -- 'sign_flip' | 'magnitude'
    note         TEXT
);

CREATE INDEX IF NOT EXISTS idx_regime_shifts_detected
    ON regime_shifts (detected_at);
