-- 075_shadow_validation.sql — MFR feed validation flags from the shadow engine
--
-- One row per (ticker, snapshot_date) that was VALIDATED. A row exists only
-- when the comparison actually ran; skipped comparisons (no MFR row, weekend
-- carry-forward, shadow not computable) are not recorded as passes.
--
-- Tolerances are EMPIRICAL (p95 of observed clean-reference divergence, see
-- shadow_params.json), not the shipped fixed defaults. A flag therefore means
-- "beyond normal disagreement", not "any disagreement".

CREATE TABLE IF NOT EXISTS shadow_validation (
    ticker         text        NOT NULL,
    snapshot_date  date        NOT NULL,

    flagged        boolean     NOT NULL DEFAULT false,
    flags          text[],                  -- machine-readable codes: rp_divergence, width_divergence, inverted, price_outside
    detail         text,                    -- human-readable, from validate_feed()

    mfr_low        numeric,
    mfr_high       numeric,
    mfr_rp         numeric,
    shadow_low     numeric,
    shadow_high    numeric,
    shadow_rp      numeric,
    rp_diff        numeric,                 -- |shadow_rp - mfr_rp|
    width_ratio    numeric,                 -- symmetric: max(mfr_w/shd_w, shd_w/mfr_w)

    rp_tol         numeric,                 -- tolerances in force for this row
    width_tol      numeric,
    params_hash    text,
    checked_at     timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_shadow_validation_flagged
    ON shadow_validation (snapshot_date DESC) WHERE flagged;

COMMENT ON TABLE shadow_validation IS
    'Shadow-vs-MFR divergence flags. Weekend carry-forward rows are excluded '
    'from validation (Sat/Sun/Mon ingest repeats Friday EOD, and a repeated '
    'range is not evidence of a bad feed). Expected false-positive floor is '
    '~8.6% of validated names - see shadow_params.json validator._note.';
