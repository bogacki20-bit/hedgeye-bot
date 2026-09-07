-- 090_ml_targets.sql — Round-2 Phase A Step 2 (docs/PROMPT_for_sean_ml_pipeline_v1.md).
-- Targets computed from bars STRICTLY AFTER D (close D = entry reference);
-- they are labels, never features — no known_at column on purpose, and the
-- walk-forward purges 30 bars at every split boundary.
-- rv20 (realized 20d vol at D, stdev of the trailing 20 daily close returns,
-- ddof=1, unannualized) is ALSO a feature (operator addition, README_SEAN.md)
-- -> new ml_features column.
--
-- Apply via:  py apply_migration.py migrations/090_ml_targets.sql

CREATE TABLE IF NOT EXISTS ml_targets (
    ticker   text NOT NULL,
    bar_date date NOT NULL,
    fwd_ret_10 numeric, fwd_ret_20 numeric, fwd_ret_30 numeric,
    fwd_mfe_20 numeric, fwd_mae_20 numeric,
    -- TP +5% before SL -2.5% within 30 bars, conservative (per bar the SL is
    -- checked FIRST, so a bar that touches both counts as SL — mirrors
    -- TrendSpider's "conservative mode (SL never hit)"). 1/0; NULL when the
    -- window is truncated by end-of-data without resolving.
    rr_hit_5_2p5_30 numeric,
    -- primary ranking target: fwd_ret_20 / rv20(D)
    fwd_sharpe_20 numeric,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_date)
);

ALTER TABLE ml_features ADD COLUMN IF NOT EXISTS rv20 numeric;
