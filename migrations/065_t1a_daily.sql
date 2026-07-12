-- 065_t1a_daily.sql — Tier One Alpha deep parse (sprint follow-on, 7/12).
--
-- One structured fact row per T1A Market Situation Report, parsed by
-- PYTHON REGEX from the OCR'd upload text (doc_uploads kind=tier1alpha) —
-- the LLM only transcribed pixels; every field here is regex-extracted
-- and every derived number is Python math. OCR loses decimal points on
-- percentages, so ratio fields carry a scale_suspect flag rather than a
-- guessed correction (facts stay honest). Prices survive OCR cleanly.

BEGIN;

CREATE TABLE IF NOT EXISTS t1a_daily (
    report_date     DATE PRIMARY KEY,
    gamma_regime    TEXT,          -- 'positive' | 'negative'   (from prose)
    systematic_bias TEXT,          -- 'buyers' | 'sellers'      (from prose)
    strategic_regime TEXT,         -- 'risk_on' | 'neutral' | 'risk_off'
    last_price      NUMERIC,
    upper_pv        NUMERIC,
    lower_pv        NUMERIC,
    gex_flip        NUMERIC,       -- 'GEX Price' = the gamma flip level
    gex_throttle    NUMERIC,       -- >0 compression, <0 expansion
    support_strike  NUMERIC,
    focal_strike    NUMERIC,
    resistance_strike NUMERIC,
    upside_risk     NUMERIC,       -- raw as printed (may be scale-suspect)
    downside_risk   NUMERIC,
    core_pct        NUMERIC,
    low_beta_pct    NUMERIC,
    flip_dist_pct   NUMERIC,       -- Python: (last-flip)/last*100
    scale_suspect   BOOLEAN NOT NULL DEFAULT FALSE,
    events          JSONB,         -- [{date, event, impact, exp_move}]
    doc_id          BIGINT,        -- source doc_uploads row
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
