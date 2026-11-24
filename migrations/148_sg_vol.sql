-- 11/24/2026: SpotGamma Volatility Dashboard, daily per name (tools/sg_vol_capture.js -> tools/sg_vol.py, which creates these too).
CREATE TABLE IF NOT EXISTS sg_vol_term (
    trade_date date NOT NULL, sym text NOT NULL, expiry date NOT NULL,
    dte numeric, iv numeric, fiv numeric, lo numeric, hi numeric, captured_at timestamptz,
    PRIMARY KEY (trade_date, sym, expiry)
);
CREATE TABLE IF NOT EXISTS sg_vol_skew (
    trade_date date NOT NULL, sym text NOT NULL, expiry date NOT NULL, strike numeric NOT NULL,
    moneyness numeric, delta numeric, iv numeric, p10 numeric, p25 numeric, median numeric, p75 numeric, p90 numeric,
    min_iv numeric, max_iv numeric, captured_at timestamptz,
    PRIMARY KEY (trade_date, sym, expiry, strike)
);
CREATE TABLE IF NOT EXISTS sg_vol_vix (
    trade_date date NOT NULL, contract text NOT NULL, quote_date date NOT NULL,
    expiry date, close numeric, quote_time timestamptz, captured_at timestamptz,
    PRIMARY KEY (trade_date, contract, quote_date)
);
CREATE TABLE IF NOT EXISTS sg_vol_daily (
    trade_date date NOT NULL, sym text NOT NULL, px numeric,
    iv30 numeric, iv30_lo numeric, iv30_hi numeric, iv30_pos numeric,
    front_iv numeric, front_dte numeric, iv90 numeric, slope numeric,
    skew_expiry date, atm_iv numeric, atm_p10 numeric, atm_median numeric, atm_p90 numeric, atm_pos numeric,
    put_skew numeric, captured_at timestamptz,
    PRIMARY KEY (trade_date, sym)
);
