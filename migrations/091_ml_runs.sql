-- 091_ml_runs.sql — Round-2 Phase A Step 4: run registry for the
-- walk-forward (ml/walkforward.py). One row per run; fold metrics and
-- feature importance land as JSON so reports/ml_round2_phaseA_*.md can be
-- regenerated from the corpus.
--
-- Apply via:  py apply_migration.py migrations/091_ml_runs.sql

CREATE TABLE IF NOT EXISTS ml_runs (
    run_id     text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    params     jsonb NOT NULL,
    metrics    jsonb NOT NULL,   -- per setup/model/fold + pooled summaries
    importance jsonb NOT NULL    -- LightGBM gain per fold
);
