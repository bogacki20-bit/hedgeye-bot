-- 076_shadow_scorecard.sql — SHADOW WATCH PHASE instrumentation
--
-- One row per computed scorecard so the 2026-08-08 review is queryable rather
-- than a re-derivation. Every headline number is stored with its own n, because
-- early in the watch phase n is tiny and the rates are not yet readable.
--
-- Nothing here feeds gating or sizing. Read-only instrumentation.

CREATE TABLE IF NOT EXISTS shadow_scorecard (
    as_of              date        PRIMARY KEY,
    computed_at        timestamptz NOT NULL DEFAULT now(),

    -- 1. FORWARD COVERAGE — next-day close inside the prior day's shadow band.
    --    Target band 88-92%. Persistently >94% means bands are too wide.
    coverage_pct       numeric,
    coverage_n         integer,
    coverage_by_class  jsonb,      -- {equity: {pct, n}, etf: {...}, ...}
    coverage_verdict   text,       -- in_band | too_wide | too_tight | insufficient_n

    -- 2. EXTREME BEHAVIOUR — did rp<=0.20 bounce, did rp>=0.80 stall/fade.
    low_n              integer,
    low_hit_pct        numeric,    -- % of rp<=0.20 prints with a positive fwd-5 return
    low_avg_fwd5       numeric,
    high_n             integer,
    high_hit_pct       numeric,    -- % of rp>=0.80 prints with a non-positive fwd-5 return
    high_avg_fwd5      numeric,

    -- 3. VALIDATOR HEALTH — vs the 8.6% empirical false-positive floor.
    validated_n        integer,
    flagged_n          integer,
    flag_rate          numeric,
    n_range_break      integer,    -- signal, not fault
    n_stale_band       integer,    -- rising = MFR coverage decaying further
    n_divergence       integer,

    -- 4. SHD-SOURCED NAMES — where shadow is actually load-bearing.
    shd_names_n        integer,
    shd_coverage_pct   numeric,
    shd_coverage_n     integer,

    payload            jsonb,      -- full detail: per-name lists, days-on-shadow
    notes              text
);

CREATE INDEX IF NOT EXISTS idx_shadow_scorecard_asof
    ON shadow_scorecard (as_of DESC);

COMMENT ON TABLE shadow_scorecard IS
    'Shadow watch-phase scorecard, one row per computation. Review date '
    '2026-08-08 (two full trading weeks live). Every rate is stored beside its '
    'n - early rows have n small enough that the rates should not be read.';
