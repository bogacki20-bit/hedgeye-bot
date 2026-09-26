-- 112: Tier1 Alpha Market Situation Report — daily key levels + systematic
-- positioning (build list #17). One row per report date. The full MSR PDF is
-- image-only, so values are read off page images (parser_t1a.py) and
-- sanity-checked before insert. Reversible: DROP TABLE t1a_snapshots.
CREATE TABLE IF NOT EXISTS t1a_snapshots (
    report_date          DATE PRIMARY KEY,      -- the MSR's dated edition (morning)
    data_date            DATE,                  -- the close its charts use (prior session)
    source_email_id      TEXT,
    capture_path         TEXT,                  -- 'chrome_tab' | 'harvester_pdf'
    -- page 1: status grid
    gamma_exposure       TEXT,                  -- Positive | Neutral | Negative
    systematic_flow_risk TEXT,                  -- Bullish | Neutral | Bearish
    pv_band_rr           TEXT,                  -- Long | Neutral | Short
    strategic_allocation TEXT,                  -- Risk On | Neutral | Risk Off
    -- page 1: SPX key levels and strikes
    spx_last             NUMERIC,
    upper_pv_band        NUMERIC,
    lower_pv_band        NUMERIC,
    upside_risk_pct      NUMERIC,
    downside_risk_pct    NUMERIC,
    gex_throttle         NUMERIC,
    gex_flip             NUMERIC,
    implied_move_pct     NUMERIC,
    resistance_strike    NUMERIC,
    focal_strike         NUMERIC,
    support_strike       NUMERIC,
    -- page 4: systematic positioning
    sys_positioning_z    NUMERIC,               -- read off the chart: approximate
    vc_flow_by_move      JSONB,                 -- {"-5": -100.3, ..., "5": -100.9} $bn
    -- page 5: other indexes
    ndx_zero_gamma       NUMERIC,
    ndx_upper_pv         NUMERIC,
    ndx_lower_pv         NUMERIC,
    rut_zero_gamma       NUMERIC,
    rut_upper_pv         NUMERIC,
    rut_lower_pv         NUMERIC,
    raw                  JSONB,                 -- full extractor output
    parsed_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
