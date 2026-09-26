-- 113: SpotGamma index key levels, one row per (note date, symbol).
-- Source: the daily Founder's Note level table (canary founders_note capture,
-- data/snapshots/spotgamma/<date>/founders_note_pm.md) — until now these lived
-- only in markdown, so nothing could compare them over time (T1A-vs-SG flip
-- study, desk 9/26). sg_levels (never written) is left alone.
-- Reversible: DROP TABLE sg_index_levels.
CREATE TABLE IF NOT EXISTS sg_index_levels (
    note_date          DATE NOT NULL,        -- Founder's Note edition (AM); levels = prior close
    sym                TEXT NOT NULL,        -- ES | SPX | SPY | NDX | QQQ | RUT | IWM
    ref_price          NUMERIC,
    vol_trigger        NUMERIC,
    zero_gamma         NUMERIC,
    call_wall          NUMERIC,
    put_wall           NUMERIC,
    abs_gamma_strike   NUMERIC,
    gamma_tilt         NUMERIC,
    gamma_notional_mm  NUMERIC,              -- $ millions, signed
    rr25               NUMERIC,              -- 25-delta risk reversal
    implied_move_high  NUMERIC,              -- SPX / SPY only
    implied_move_low   NUMERIC,
    risk_pivot         NUMERIC,              -- SPX only (SG Summary "Pivot:")
    source_file        TEXT,
    parsed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (note_date, sym)
);
