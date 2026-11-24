-- 11/24/2026 (Kris: "I want to track the rate of change during the day"): the SpotGamma Volatility Dashboard is now
-- captured many times a day. sg_vol_daily / _term / _skew / _vix keep only the day's latest run (keyed on trade_date);
-- these two keep every run. tools/sg_vol.py creates them too and fills them on every --ingest.
CREATE TABLE IF NOT EXISTS sg_vol_intraday (
    as_of timestamptz NOT NULL, sym text NOT NULL, trade_date date NOT NULL, px numeric,
    iv30 numeric, iv30_lo numeric, iv30_hi numeric, iv30_pos numeric,
    front_iv numeric, front_dte numeric, iv90 numeric, slope numeric,
    skew_expiry date, atm_iv numeric, atm_p10 numeric, atm_median numeric, atm_p90 numeric, atm_pos numeric,
    put_skew numeric, captured_at timestamptz, source_file text,
    PRIMARY KEY (as_of, sym)
);
CREATE INDEX IF NOT EXISTS sg_vol_intraday_sym_day ON sg_vol_intraday (sym, trade_date, as_of);
CREATE TABLE IF NOT EXISTS sg_vol_vix_intraday (
    as_of timestamptz NOT NULL, contract text NOT NULL, quote_date date NOT NULL,
    trade_date date, expiry date, close numeric, quote_time timestamptz, source_file text,
    PRIMARY KEY (as_of, contract, quote_date)
);
