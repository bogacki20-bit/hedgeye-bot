-- 121: IBKR price feed from IB Gateway on the Lenovo (canary ibkr_streamer.py).
--  * ibkr_quotes: latest IBKR quote per bot ticker, every data type (live,
--    frozen, delayed, delayed-frozen). The streamer ALSO upserts live_quotes
--    (source 'ibkr') but only for LIVE quotes, so 15-min delayed data never
--    displaces Yahoo; once the real-time subscription is on, v_screener and
--    everything reading live_quotes switch to IBKR with no code change.
--  * ibkr_bars: intraday bars (5 min) for the book + HUD indices, for the
--    glasses chart; bar_start is UTC.
-- Reversible: DROP TABLE ibkr_quotes, ibkr_bars.

CREATE TABLE IF NOT EXISTS ibkr_quotes (
    ticker      TEXT PRIMARY KEY,        -- bot ticker (SPX, ^VIX, CL_F, AAPL ...)
    price       NUMERIC NOT NULL,        -- last, else mid, else close
    bid         NUMERIC,
    ask         NUMERIC,
    last        NUMERIC,
    prev_close  NUMERIC,
    volume      NUMERIC,
    data_type   TEXT NOT NULL,           -- live | frozen | delayed | delayed-frozen
    con_id      BIGINT,
    quote_at    TIMESTAMPTZ NOT NULL,    -- when the tick arrived (delayed = ~15 min old)
    polled_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ibkr_quotes_polled ON ibkr_quotes (polled_at DESC);

CREATE TABLE IF NOT EXISTS ibkr_bars (
    ticker      TEXT NOT NULL,
    bar_size    TEXT NOT NULL,           -- '5 mins'
    bar_start   TIMESTAMPTZ NOT NULL,
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC,
    volume      NUMERIC,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_size, bar_start)
);
CREATE INDEX IF NOT EXISTS ibkr_bars_fetched ON ibkr_bars (fetched_at DESC);
