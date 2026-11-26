-- 153: hard data under the flow tracker, all assets (Kris 2026-11-26).
--   sys_flow_cot : CFTC weekly positions (leveraged funds / managed money), 3 years kept
--   sys_flow_fit : exposures solved from real managed-futures fund returns, one row per bar + market
--   sys_flow_fund_holdings.market : our market key for each futures line (KMLM and Simplify CTA added)
-- Reversible: DROP the two tables, DROP COLUMN market.
BEGIN;

CREATE TABLE IF NOT EXISTS sys_flow_cot (
    report_date  date NOT NULL,     -- the Tuesday the positions are counted
    code         text NOT NULL,     -- CFTC contract market code
    market       text NOT NULL,     -- our key (tools/sys_flows_markets.py)
    trader_group text,              -- 'leveraged funds' | 'managed money'
    pos_long     bigint,
    pos_short    bigint,
    net          bigint,            -- contracts, long - short
    oi           bigint,            -- open interest, all traders
    captured_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (report_date, code)
);

CREATE TABLE IF NOT EXISTS sys_flow_fit (
    bar_date    date NOT NULL,
    market      text NOT NULL,
    beta_pct    numeric,            -- implied exposure, % of fund (- = short)
    r2          numeric,            -- share of the funds' daily moves the mix explains
    window_days int,
    n_funds     int,
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bar_date, market)
);

ALTER TABLE sys_flow_fund_holdings ADD COLUMN IF NOT EXISTS market text;

COMMIT;
