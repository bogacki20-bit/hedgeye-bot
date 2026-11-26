-- 152: daily holdings of managed-futures ETFs (DBMF first), read off the fund's own page by
-- tools/sys_flows.py (Kris 2026-11-26). The closest thing to seeing real CTA positions.
-- One row per fund, holdings date and line. Reversible: DROP TABLE sys_flow_fund_holdings.
BEGIN;

CREATE TABLE IF NOT EXISTS sys_flow_fund_holdings (
    fund         text NOT NULL,     -- 'DBMF'
    as_of        date NOT NULL,     -- the holdings date the fund prints
    security     text NOT NULL,     -- 'US 2YR NOTE (CBT) MAR27'
    cusip        text NOT NULL DEFAULT '',
    ticker       text,              -- 'TUH7'
    quantity     numeric,
    market_value numeric,           -- futures: notional, $ (negative = short)
    weight_pct   numeric,           -- notional as % of the fund (negative = short)
    captured_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fund, as_of, security, cusip)
);

COMMIT;
