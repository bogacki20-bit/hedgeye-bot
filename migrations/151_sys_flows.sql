-- 151: our own systematic-flow tracker (Kris 2026-11-26: track vol-control funds and CTAs
-- without relying on Tier 1 Alpha). Written by tools/sys_flows.py. One row per S&P bar
-- (vol control) and one row per bar per market (CTA trend). Reversible: DROP both tables.
BEGIN;

CREATE TABLE IF NOT EXISTS sys_flow_volcontrol (
    bar_date        date PRIMARY KEY,
    spx_close       numeric,
    rv_1m           numeric,   -- 20-bar realized vol, zero mean, annualized %
    rv_3m           numeric,   -- 62-bar realized vol, annualized %
    rv_used         numeric,   -- max(rv_1m, rv_3m): the one the funds size on
    notional_bn     numeric,   -- k_fit / rv_used: implied equity exposure, $bn (T1A scale)
    notional_chg_bn numeric,   -- vs prior bar: + = they bought, - = they sold
    exp_t10         numeric,   -- % equity weight of a 10%-target fund (cap 150)
    exp_t12         numeric,
    exp_t15         numeric,
    flow_by_move    jsonb,     -- {"-5": $bn, ..., "5": $bn} flow if the NEXT bar moves that %
    dropoff         jsonb,     -- [{t, d1m, drop1m, d3m, drop3m}] returns leaving the windows
    k_fit           numeric,   -- scale fitted to T1A's vc_notional_bn history
    computed_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sys_flow_cta (
    bar_date    date NOT NULL,
    symbol      text NOT NULL,   -- Yahoo symbol
    label       text,
    asset_class text,            -- equity | bond | fx | commodity
    close       numeric,
    score       numeric,         -- -100..+100: average of the four lookback signals
    signals     jsonb,           -- {"21": 1, "63": -1, "126": 1, "252": 1}
    levels      jsonb,           -- {"21": px, ...} next bar's flip level per lookback
    next_sell   numeric,         -- nearest level below: a close under it flips one signal short
    next_buy    numeric,         -- nearest level above: a close over it flips one signal long
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (bar_date, symbol)
);

COMMIT;
