-- 142: everything else on the SpotGamma dashboard (Kris 2026-11-15: "build the rest so we get all of it").
-- Captured from Kris's Chrome by tools/sg_page_capture.js / tools/sg_report_capture.js, loaded by tools/sg_pages.py.
BEGIN;
-- Home page Index Levels / Index Metrics, one row per index per trade date (SPX, SPY, NDX, QQQ, RUT, IWM).
CREATE TABLE IF NOT EXISTS sg_index_daily (
    trade_date      date NOT NULL,
    sym             text NOT NULL,
    upx             numeric,
    zero_gamma      numeric,       -- zero_g_strike
    vol_trigger     numeric,       -- max_g_strike (SpotGamma's Volatility Trigger)
    call_wall       numeric, put_wall numeric,
    call_wall_gamma numeric, put_wall_gamma numeric,
    abs_gamma_strike numeric,      -- topabs_strike
    l1 numeric, l2 numeric, l3 numeric, l4 numeric,   -- Large Gamma 1-4
    c1 numeric, c2 numeric, c3 numeric, c4 numeric,   -- Combo strikes 1-4
    atm_gamma_calls numeric, atm_gamma_puts numeric,
    atm_delta_calls numeric, atm_delta_puts numeric, itm_delta numeric,
    atm_theta numeric, atm_vega numeric,
    atm_gamma_calls_n numeric, atm_gamma_puts_n numeric,
    risk_reversal   numeric,
    calls_oi numeric, puts_oi numeric, calls_vol numeric, puts_vol numeric,
    sig numeric, sig5 numeric,     -- implied 1-day / 5-day move
    gamma_notional  numeric,
    futures_sym text, futures_diff numeric,
    levels          jsonb,         -- [[strike, pct], ...] the full levels list
    gamma_curve     jsonb,         -- {strike_list, current_list, next_exp_list}
    captured_at     timestamptz,
    PRIMARY KEY (trade_date, sym)
);
-- Named data series from any page (gamma/delta by strike and expiry, level history, intraday tilt, OI by expiry,
-- absolute gamma, risk reversal, price bars, tape contract data ...). Latest capture per day wins.
CREATE TABLE IF NOT EXISTS sg_series (
    captured_date date NOT NULL,
    page        text NOT NULL,
    sym         text NOT NULL DEFAULT '',
    name        text NOT NULL,
    n           int,
    data        jsonb,
    captured_at timestamptz,
    PRIMARY KEY (captured_date, page, sym, name)
);
-- Economic / events calendar (home page).
CREATE TABLE IF NOT EXISTS sg_events (
    event_at    timestamptz NOT NULL,
    country     text NOT NULL DEFAULT '',
    event       text NOT NULL,
    currency    text, previous numeric, estimate numeric, actual numeric, change numeric, change_pct numeric,
    impact      text, unit text,
    updated_at  timestamptz DEFAULT now(),
    PRIMARY KEY (event_at, country, event)
);
-- Scanner rows (Squeeze Candidates, VRP, GARCH flags) with the full Equity Hub record per name.
CREATE TABLE IF NOT EXISTS sg_scanner_rows (
    captured_date date NOT NULL,
    sym         text NOT NULL,
    squeeze     boolean, vrp boolean, vrp_high boolean, garch boolean,
    data        jsonb,
    captured_at timestamptz,
    PRIMARY KEY (captured_date, sym)
);
-- Full Equity Hub record for a ticker (incl. 10-day history, gamma by strike, skew, DPI), whenever a page shows one.
CREATE TABLE IF NOT EXISTS sg_equity (
    trade_date  date NOT NULL,
    sym         text NOT NULL,
    data        jsonb,
    captured_at timestamptz,
    PRIMARY KEY (trade_date, sym)
);
-- Reports: FlowPatrol, Opening Setup, Sunday Note (full text).
CREATE TABLE IF NOT EXISTS sg_reports (
    report_date date NOT NULL,
    kind        text NOT NULL,
    title       text,
    url         text,
    pages       int,
    text        text,
    captured_at timestamptz,
    PRIMARY KEY (report_date, kind)
);
-- One row per page capture (what was saved, how big), so feed_health can see each page landed.
CREATE TABLE IF NOT EXISTS sg_page_captures (
    captured_at timestamptz NOT NULL,
    page        text NOT NULL,
    sym         text NOT NULL DEFAULT '',
    tab         text NOT NULL DEFAULT '',
    file        text,
    n_states    int, n_grids int, n_series int, bytes int,
    PRIMARY KEY (captured_at, page, sym, tab)
);
COMMIT;
