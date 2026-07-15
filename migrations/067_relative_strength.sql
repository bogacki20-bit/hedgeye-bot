-- ============================================================================
-- Migration 067 — relative strength + sector-correlation matrix (2026-07-14)
--
-- Roadmap tier 1 ("sectors-first correlation build"). Two new tables plus
-- reuse of the existing correlation_snapshots (migration 011) for the raw
-- pairwise sector correlations.
--
--   rs_snapshots — one row per (snapshot_date, ticker, benchmark). Holds the
--     relative-strength read of a name/sector against a benchmark (default
--     SPY) across three Hedgeye-style durations plus the RS slope (the
--     "is the leader rolling over?" tell), the 1..N rank per duration, the
--     joined Hedgeye range position (rp), and the 4-cell grid classification
--     (PASS_THE_PUCK / HOLD / TRAP / FADE). This is "the puck" table: rp says
--     WHERE a name sits in its range, RS says whether it is a leader or a
--     falling knife.
--
--   diversification_snapshots — one row per (snapshot_date, window_days).
--     The average pairwise correlation across the sector universe, used as a
--     crowding / diversification-regime gauge. High avg corr = fake
--     diversification (the energy-lesson failure mode); low = real dispersion.
--     The per-pair correlations themselves live in correlation_snapshots.
--
-- Apply via:  py apply_migration.py migrations/067_relative_strength.sql
-- All statements idempotent (CREATE IF NOT EXISTS) — safe to re-run.
-- ============================================================================

-- ── Relative strength ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rs_snapshots (
    id            BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    ticker        TEXT NOT NULL,
    benchmark     TEXT NOT NULL DEFAULT 'SPY',

    -- RS ratio = close(ticker) / close(benchmark). The three durations are the
    -- percent change of that ratio over the trailing window (relative perf).
    rs_trade      NUMERIC(10,6),   -- ~10 sessions   (Hedgeye TRADE)
    rs_trend      NUMERIC(10,6),   -- ~60 sessions   (Hedgeye TREND)
    rs_tail       NUMERIC(10,6),   -- ~200 sessions  (Hedgeye TAIL)

    -- Normalized per-day OLS slope of the RS ratio over the short window.
    -- Sign is the tell: a name can rank #1 on rs_trend and still be topping
    -- (rs_slope < 0 = rolling over).
    rs_slope      NUMERIC(12,8),

    -- 1 = strongest. Ranked within the run's universe, per duration.
    rank_trade    INTEGER,
    rank_trend    INTEGER,
    rank_tail     INTEGER,
    universe_n    INTEGER,         -- how many names were ranked this run

    -- Range position at snapshot time, joined from mfr_snapshots
    -- (range_low/range_high) — the same stored range source REPORT / REPORT NOW
    -- use: rp = (price - range_low)/(range_high - range_low). May be NULL (no
    -- range) or outside [0,1] (range broken/stale — NOT voided here, that is the
    -- caller's decision per range-age rules).
    rp            NUMERIC(10,6),
    range_broken  BOOLEAN,         -- TRUE when rp < 0 or rp > 1

    -- 4-cell grid: PASS_THE_PUCK (RS high + rp low = leader on sale),
    -- HOLD (RS high + rp high), TRAP (RS low + rp low = knife),
    -- FADE (RS low + rp high). NULL when rp is unavailable.
    grid_cell     TEXT,
    rolling_over  BOOLEAN,         -- RS high on rank but rs_slope negative

    n_obs         INTEGER,         -- aligned observations used for the ratio
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, ticker, benchmark)
);

CREATE INDEX IF NOT EXISTS idx_rs_snap_date
    ON rs_snapshots (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_rs_snap_ticker
    ON rs_snapshots (ticker, benchmark, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_rs_snap_grid
    ON rs_snapshots (snapshot_date, grid_cell);

-- ── Diversification / crowding gauge ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS diversification_snapshots (
    id                BIGSERIAL PRIMARY KEY,
    snapshot_date     DATE NOT NULL,
    window_days       INTEGER NOT NULL,
    universe          TEXT NOT NULL DEFAULT 'sector_spdr',
    avg_pairwise_corr NUMERIC(8,5),   -- mean of all off-diagonal pair corrs
    max_pairwise_corr NUMERIC(8,5),
    min_pairwise_corr NUMERIC(8,5),
    n_pairs           INTEGER,
    -- 'tight'  (avg >= 0.70) — crowded, fake-diversification risk
    -- 'normal' (0.40..0.70)
    -- 'loose'  (avg < 0.40)  — real dispersion
    regime            TEXT,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, window_days, universe)
);

CREATE INDEX IF NOT EXISTS idx_div_snap_date
    ON diversification_snapshots (snapshot_date DESC, window_days);
