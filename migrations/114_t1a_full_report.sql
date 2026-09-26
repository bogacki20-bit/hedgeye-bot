-- 114: store the WHOLE Tier1 Alpha MSR (desk 9/26: "we basically want all the
-- data from Tier1 Alpha"), plus our own exactly-computed SPX realized vol so
-- the 1m/3m cross is dated to the day, not read off a chart line.
-- Chart-read values (a line's last point read off the axis) are approximate;
-- raw->'approx_fields' lists them per row. Reversible: DROP the added columns
-- and DROP TABLE spx_realized_vol.

ALTER TABLE t1a_snapshots
    -- p2: short-dated vol term structure + event calendar
    ADD COLUMN IF NOT EXISTS term_structure    JSONB,   -- [{expiry, iv}]
    ADD COLUMN IF NOT EXISTS event_calendar    JSONB,   -- [{date, event, estimate, previous, impact, call_iv, put_iv, spx_iv, expected_move_pct, pc_vol, pc_oi}]
    -- p3: dealer gamma profile
    ADD COLUMN IF NOT EXISTS gex_per_1pct_bn   NUMERIC,
    ADD COLUMN IF NOT EXISTS rv_10d            NUMERIC,
    -- p4: systematic positioning (chart reads = approx)
    ADD COLUMN IF NOT EXISTS rv_1m_t1a         NUMERIC,
    ADD COLUMN IF NOT EXISTS rv_3m_t1a         NUMERIC,
    ADD COLUMN IF NOT EXISTS vc_notional_bn    NUMERIC, -- Vol Control implied notional equity exposure
    ADD COLUMN IF NOT EXISTS cta_position      NUMERIC, -- CTA positioning in US equities (index)
    ADD COLUMN IF NOT EXISTS rv_dropoff        JSONB,   -- [{t, d1m, drop1m, d3m, drop3m}]
    -- p5: strikes in play per index
    ADD COLUMN IF NOT EXISTS strikes_in_play   JSONB,   -- {"SPX": [...], "NDX": [...], "RUT": [...]}
    -- p6: sector probable volatility bands
    ADD COLUMN IF NOT EXISTS sector_bands      JSONB,   -- [{asset, last, lower, upper, upside_pct, downside_pct, spread_pct, rvol_1m, beta_2y}]
    -- p7: breadth
    ADD COLUMN IF NOT EXISTS stocks_up         INT,
    ADD COLUMN IF NOT EXISTS stocks_down       INT,
    ADD COLUMN IF NOT EXISTS spx_daily_pct     NUMERIC,
    ADD COLUMN IF NOT EXISTS avg_return_pct    NUMERIC,
    ADD COLUMN IF NOT EXISTS avg_gain_pct      NUMERIC,
    ADD COLUMN IF NOT EXISTS avg_decline_pct   NUMERIC,
    ADD COLUMN IF NOT EXISTS breadth_20d_pct   NUMERIC, -- % of SPX above 20d MA (approx)
    ADD COLUMN IF NOT EXISTS breadth_50d_pct   NUMERIC,
    ADD COLUMN IF NOT EXISTS breadth_200d_pct  NUMERIC,
    ADD COLUMN IF NOT EXISTS impact_leaders    JSONB;   -- [{side, ticker, ret_pct, impact_pct, weight_pct}]

-- Our own SPX realized vol (annualized close-to-close stdev, Yahoo ^GSPC).
CREATE TABLE IF NOT EXISTS spx_realized_vol (
    trade_date   DATE PRIMARY KEY,
    spx_close    NUMERIC,
    rv_10d       NUMERIC,
    rv_1m        NUMERIC,      -- 21 sessions
    rv_3m        NUMERIC,      -- 63 sessions
    spread_1m_3m NUMERIC,      -- rv_1m - rv_3m (sign change = cross)
    crossed      TEXT,         -- 'up' (1m crossed above 3m) | 'down' | NULL
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
