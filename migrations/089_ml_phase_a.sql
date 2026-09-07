-- 089_ml_phase_a.sql — Round-2 Phase A: our own ranking model (ml/ brief,
-- 2026-09-07). px_daily bars + ml_features. Targets/runs tables arrive with
-- their steps (090+). Doctrine: corpus-first, honest known_at, no lookahead.

-- Daily OHLCV. Five enrolled tickers come from the TradingView CSVs
-- (data/tradingview/, source='tradingview'); HYG and ^VIX daily history from
-- yfinance with auto_adjust=False (source='yfinance-unadjusted'; ^VIX has no
-- volume). ^VIX is the INDEX itself — not the range-midpoint proxy the
-- TrendSpider scripts were forced to use.
CREATE TABLE IF NOT EXISTS px_daily (
    ticker    text    NOT NULL,
    bar_date  date    NOT NULL,
    o numeric, h numeric, l numeric, c numeric NOT NULL, v numeric,
    source    text    NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_date)
);

-- One row per ticker per bar. Every feature at bar D uses data with
-- known_at <= D 16:00 ET only (ranges dated D are published the prior
-- evening — allowed); known_at = bar_date 16:00 ET because close[D] enters
-- rp/trend_dist/context. source: 'tv' (TV indicator history),
-- 'live' (ranges from mfr_snapshots after the ticker's first live date,
-- same union rule as trendspider_export.py), 'tv_unverified' (TLT — the
-- 2026-09-06 waiver: indicator-only, feed_verified=false).
CREATE TABLE IF NOT EXISTS ml_features (
    ticker   text NOT NULL,
    bar_date date NOT NULL,
    known_at timestamptz NOT NULL,
    source   text NOT NULL,
    -- state
    rp numeric, rp_dev20 numeric, bulldist numeric, rng_width numeric,
    ltrp numeric, hurst64 numeric, hurst256 numeric,
    trend_dist numeric, trade_dist numeric, above_trend numeric,
    vixfix numeric, volatility numeric,
    buy numeric, mega_buy numeric, sell numeric, mega_sell numeric,
    -- rate of change
    rp_d3 numeric, bulldist_d3 numeric, hi_d3 numeric, lo_d3 numeric,
    trend_dist_d3 numeric, hurst64_d5 numeric, rng_width_d5 numeric,
    -- volume (tools.volume_signal, verbatim functions)
    decel_streak numeric, distribution numeric,
    -- context (same for every ticker on a date)
    vix_level numeric, vix_bucket numeric, vix_roc5 numeric,
    hyg_roc10 numeric, uup_rp numeric, uup_rp_d3 numeric,
    -- cross-asset
    corr30_spy numeric, corr30_uup numeric, usd_pressure numeric,
    built_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_date)
);

CREATE INDEX IF NOT EXISTS idx_ml_features_date ON ml_features (bar_date);
