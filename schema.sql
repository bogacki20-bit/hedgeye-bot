-- ============================================================================
-- HEDGEYE BOT — POSTGRES SCHEMA
-- ============================================================================
-- Email lake architecture per master context Section 9.
-- All statements idempotent (CREATE TABLE IF NOT EXISTS) — safe to re-run.
-- Apply via: railway run psql $DATABASE_URL -f schema.sql
-- ============================================================================


-- ============================================================================
-- EMAIL LAKE — single source of truth for every Hedgeye email
-- ============================================================================
-- Specialized parsers extract from this raw table into the typed tables below.
-- Adding a new parser means re-reading the lake; no IMAP refetch required.
--
-- Some Hedgeye emails are TEASERS — the email body contains a "click here to
-- read the full report" link, with the actual report behind the portal login.
-- Schema handles this with three fields:
--   full_report_url        — captured from the teaser email
--   full_report_html       — null until a fetcher pulls it (Phase 2 work)
--   content_status         — tracks whether we have the full content yet
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_emails_raw (
    message_id              TEXT PRIMARY KEY,           -- RFC 5322 Message-ID
    imap_uid                TEXT,                        -- IMAP UID (folder-specific, for resumable backfill)
    sender                  TEXT NOT NULL,
    subject                 TEXT,
    received_at             TIMESTAMPTZ NOT NULL,
    html_body               TEXT,
    text_body               TEXT,
    full_report_url         TEXT,                        -- "click to read more" link extracted from teaser
    full_report_html        TEXT,                        -- populated later when fetcher exists
    full_report_fetched_at  TIMESTAMPTZ,
    content_status          TEXT NOT NULL DEFAULT 'unknown'
        CHECK (content_status IN ('complete','teaser','fetched','failed','unknown')),
    classified_as           TEXT,                        -- risk_range / etf_pro / early_look / macro_show / quad_nowcast / signal_change / other
    classifier_confidence   REAL,
    raw_size_bytes          INTEGER,
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    parsed_at               TIMESTAMPTZ                  -- null = not yet parsed by a typed-table parser
);

CREATE INDEX IF NOT EXISTS idx_emails_received    ON hedgeye_emails_raw(received_at);
CREATE INDEX IF NOT EXISTS idx_emails_classified  ON hedgeye_emails_raw(classified_as);
CREATE INDEX IF NOT EXISTS idx_emails_status      ON hedgeye_emails_raw(content_status);
CREATE INDEX IF NOT EXISTS idx_emails_unparsed    ON hedgeye_emails_raw(received_at) WHERE parsed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_emails_teasers     ON hedgeye_emails_raw(received_at)
    WHERE content_status = 'teaser' AND full_report_html IS NULL;


-- ============================================================================
-- IMAP BACKFILL STATE — resumable 4-year backfill across iCloud rate limits
-- ============================================================================

CREATE TABLE IF NOT EXISTS imap_backfill_state (
    folder              TEXT PRIMARY KEY,                -- "INBOX" or future VIP folder
    earliest_uid_seen   BIGINT,
    earliest_date_seen  TIMESTAMPTZ,
    latest_uid_seen     BIGINT,
    latest_date_seen    TIMESTAMPTZ,
    last_run_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_status         TEXT,                            -- "ok" / "rate_limited" / "error: ..."
    total_fetched       BIGINT DEFAULT 0,
    notes               TEXT
);


-- ============================================================================
-- PARSED TABLES — typed extracts from hedgeye_emails_raw
-- ============================================================================
-- One parser per email type. Each row carries source_email_id so we can
-- always trace a typed row back to the raw email it came from.
-- ============================================================================

-- Risk Range Signals — daily ~7:48 AM EDT, 35+ tickers
CREATE TABLE IF NOT EXISTS hedgeye_risk_ranges (
    ticker          TEXT NOT NULL,
    signal_date     DATE NOT NULL,
    trend           TEXT,                                -- BULLISH / BEARISH / NEUTRAL
    buy_trade       NUMERIC(14,4),                       -- low end of risk range
    sell_trade      NUMERIC(14,4),                       -- high end of risk range
    prev_close      NUMERIC(14,4),
    description     TEXT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id) ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_rr_ticker ON hedgeye_risk_ranges(ticker);
CREATE INDEX IF NOT EXISTS idx_rr_date   ON hedgeye_risk_ranges(signal_date);
CREATE INDEX IF NOT EXISTS idx_rr_trend  ON hedgeye_risk_ranges(trend);


-- ETF Pro — Monday emails, 18 tickers, ranges go stale by Wednesday
CREATE TABLE IF NOT EXISTS hedgeye_etf_pro_ranges (
    ticker          TEXT NOT NULL,
    week_of         DATE NOT NULL,                       -- Monday of the week the range applies to
    range_low       NUMERIC(14,4),
    range_high      NUMERIC(14,4),
    description     TEXT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id) ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, week_of)
);

CREATE INDEX IF NOT EXISTS idx_etf_ticker ON hedgeye_etf_pro_ranges(ticker);
CREATE INDEX IF NOT EXISTS idx_etf_week   ON hedgeye_etf_pro_ranges(week_of);


-- Signal Changes — TREND CHANGE blocks at top of Risk Range emails, plus OutBucket additions
CREATE TABLE IF NOT EXISTS hedgeye_signal_changes (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    change_type     TEXT NOT NULL,                       -- trend_change / out_bucket / new_addition
    prev_state      TEXT,
    new_state       TEXT,
    signal_date     DATE NOT NULL,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id) ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, change_type, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_sc_ticker ON hedgeye_signal_changes(ticker);
CREATE INDEX IF NOT EXISTS idx_sc_date   ON hedgeye_signal_changes(signal_date);
CREATE INDEX IF NOT EXISTS idx_sc_type   ON hedgeye_signal_changes(change_type);


-- Early Look — daily essay (often a teaser email; full content fetched later)
CREATE TABLE IF NOT EXISTS hedgeye_early_look (
    publish_date    DATE PRIMARY KEY,
    subject         TEXT,
    body_text       TEXT,                                -- nullable — empty until full report fetched
    key_tickers     TEXT[],                              -- mentioned tickers (extracted)
    word_count      INTEGER,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id) ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- Macro Show notes
CREATE TABLE IF NOT EXISTS hedgeye_macro_show_notes (
    publish_date    DATE PRIMARY KEY,
    body_text       TEXT,
    word_count      INTEGER,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id) ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- Quad Nowcast — quarterly probability mix
CREATE TABLE IF NOT EXISTS hedgeye_quad_nowcast (
    publish_date    DATE NOT NULL,
    region          TEXT NOT NULL DEFAULT 'US',          -- US / EZ / CN / etc.
    horizon_quarter TEXT,                                -- e.g. "Q2 2026"
    q1_prob         NUMERIC(6,4),
    q2_prob         NUMERIC(6,4),
    q3_prob         NUMERIC(6,4),
    q4_prob         NUMERIC(6,4),
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id) ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (publish_date, region, horizon_quarter)
);


-- ============================================================================
-- MFR SNAPSHOTS — daily MyFractalRange API pulls, full payload preserved
-- ============================================================================
-- Surface fields are denormalized from full_payload for fast querying.
-- full_payload (JSONB) holds gamma surface, ltRangeData, IV/RV history,
-- everything the API returns. Retention: forever — this is the ML corpus.
-- ============================================================================

CREATE TABLE IF NOT EXISTS mfr_snapshots (
    ticker              TEXT NOT NULL,
    snapshot_date       DATE NOT NULL,
    price               NUMERIC(14,4),
    range_low           NUMERIC(14,4),
    range_high          NUMERIC(14,4),
    trend_signal        TEXT,                            -- trendBullish / trendBearish / trendNeutral
    momentum_signal     TEXT,                            -- momentumBullish / momentumBearish
    hurst               NUMERIC(6,4),
    hurst_3mo           NUMERIC(6,4),
    iv                  NUMERIC(8,4),
    rv                  NUMERIC(8,4),
    daily_pct_change    NUMERIC(8,4),
    previous_day_volume BIGINT,
    full_payload        JSONB NOT NULL,                  -- complete API response
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_endpoint     TEXT NOT NULL DEFAULT '/v2/asset',
    PRIMARY KEY (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_mfr_ticker     ON mfr_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_mfr_date       ON mfr_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_mfr_trend      ON mfr_snapshots(trend_signal);
CREATE INDEX IF NOT EXISTS idx_mfr_payload    ON mfr_snapshots USING GIN (full_payload);


-- ============================================================================
-- SPOTGAMMA EQUITY HUB SNAPSHOTS — typed per-ticker capture
-- ============================================================================
-- Mirrors the mfr_snapshots pattern but for SpotGamma's equity-hub data.
-- Populated from data/snapshots/spotgamma/{date}/equityhub*/{TICKER}.md by
-- spotgamma_client.populate_from_snapshots, and from on-demand chrome scrapes
-- by spotgamma_client.refresh_via_chrome (slice 2). Read by
-- monitor_context.get_spotgamma_ctx as the preferred path before falling back
-- to corpus_documents regex.
--
-- capture_type values:
--   'premarket'  — from data/snapshots/spotgamma/{date}/equityhub/{TICKER}.md
--   'eod'        — from data/snapshots/spotgamma/{date}/equityhub_eod/{TICKER}.md
--   'on_demand'  — from spotgamma_client.refresh_via_chrome (slice 2)
-- ============================================================================

CREATE TABLE IF NOT EXISTS spotgamma_snapshots (
    ticker                  TEXT NOT NULL,
    snapshot_date           DATE NOT NULL,
    capture_type            TEXT NOT NULL,                   -- premarket / eod / on_demand

    -- Headline data
    price                   NUMERIC(14,4),
    prev_close              NUMERIC(14,4),
    daily_change_pct        NUMERIC(8,4),
    stock_volume            BIGINT,
    high_52w                NUMERIC(14,4),
    low_52w                 NUMERIC(14,4),
    earnings_date           DATE,

    -- Key SpotGamma levels
    call_wall               NUMERIC(14,4),
    put_wall                NUMERIC(14,4),
    hedge_wall              NUMERIC(14,4),
    key_gamma_strike        NUMERIC(14,4),
    key_delta_strike        NUMERIC(14,4),

    -- Gamma posture (call_gamma / put_gamma stored as raw $ notional —
    -- SpotGamma's "-2.31M" display is parsed to -2310000.00 here)
    call_gamma              NUMERIC(20,4),
    put_gamma               NUMERIC(20,4),
    top_gamma_exp           DATE,
    top_delta_exp           DATE,
    gamma_hedge_est         NUMERIC(14,4),

    -- Options activity (rank/percent fields stored as percent, e.g. 36.55)
    call_volume             BIGINT,
    put_volume              BIGINT,
    put_call_oi_ratio       NUMERIC(8,4),
    iv_1m                   NUMERIC(8,4),
    rv_1m                   NUMERIC(8,4),
    iv_rank                 NUMERIC(8,4),
    skew_rank               NUMERIC(8,4),
    garch_rank              NUMERIC(8,4),
    options_implied_move    NUMERIC(14,4),

    -- Provenance
    source_ref              TEXT,                            -- file path or URL
    raw_text                TEXT,                            -- full markdown body for ML fallback
    full_payload            JSONB NOT NULL,                  -- all parsed fields as dict
    fetched_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (ticker, snapshot_date, capture_type)
);

CREATE INDEX IF NOT EXISTS idx_sg_ticker   ON spotgamma_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_sg_date     ON spotgamma_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_sg_fetched  ON spotgamma_snapshots(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_sg_payload  ON spotgamma_snapshots USING GIN (full_payload);

-- Widen gamma columns if the table was created with the original NUMERIC(14,4)
-- footprint. Index ETFs (SPY/QQQ) can have $-billions of gamma which doesn't
-- fit in 14 digits before the decimal. Idempotent: ALTER ... TYPE to the same
-- shape is a no-op.
ALTER TABLE spotgamma_snapshots ALTER COLUMN call_gamma TYPE NUMERIC(20,4);
ALTER TABLE spotgamma_snapshots ALTER COLUMN put_gamma  TYPE NUMERIC(20,4);


-- ============================================================================
-- YAHOO FINANCE SNAPSHOTS — typed per-ticker daily price capture
-- ============================================================================
-- Third leg of the lockstep refresh trio (MFR + SpotGamma + Yahoo). Populated
-- by yfinance_client when Hedgeye surfaces a ticker, plus optionally by
-- price_monitor on a daily roll-up. Read by analytics + the eventual unified
-- decision layer.
--
-- capture_type values:
--   'email_arrival'      — pulled when a Hedgeye email mentions this ticker
--   'monitor_cycle'      — daily roll-up from price_monitor (overwrites today's row)
--   'on_demand'          — manual / debug fetches
--
-- We deliberately don't store every intraday cycle here — that would explode
-- table size for negligible v1 value. If a future ML run needs minute-level
-- price, we'll add a separate yahoo_intraday table partitioned by date.
-- ============================================================================

CREATE TABLE IF NOT EXISTS yahoo_snapshots (
    ticker              TEXT NOT NULL,
    snapshot_date       DATE NOT NULL,
    capture_type        TEXT NOT NULL,                   -- email_arrival / monitor_cycle / on_demand

    -- Price + change. `price` is the most recent close (live during market
    -- hours, daily close after); other OHLC fields are today's daily bar.
    price               NUMERIC(14,4),
    open                NUMERIC(14,4),
    high                NUMERIC(14,4),
    low                 NUMERIC(14,4),
    close               NUMERIC(14,4),
    prev_close          NUMERIC(14,4),
    daily_change        NUMERIC(14,4),                   -- $
    daily_change_pct    NUMERIC(8,4),                    -- %
    volume              BIGINT,

    -- Provenance
    yf_symbol           TEXT,                            -- the actual Yahoo symbol (^GSPC / CL=F / BTC-USD)
    full_payload        JSONB NOT NULL,                  -- raw OHLCV row + any extras
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (ticker, snapshot_date, capture_type)
);

CREATE INDEX IF NOT EXISTS idx_yf_ticker   ON yahoo_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_yf_date     ON yahoo_snapshots(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_yf_fetched  ON yahoo_snapshots(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_yf_payload  ON yahoo_snapshots USING GIN (full_payload);


-- ============================================================================
-- ALERTS FIRED — dedup so we don't re-alert same ticker/boundary same day
-- ============================================================================

CREATE TABLE IF NOT EXISTS alerts_fired (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    boundary        TEXT NOT NULL,                       -- range_low / range_high / mid_breach / stop_loss
    range_zone      TEXT,                                -- top_third / middle_third / bottom_third
    signal_date     DATE NOT NULL,                       -- the Risk Range signal_date the alert was based on
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    price_at_fire   NUMERIC(14,4),
    range_at_fire   JSONB,                               -- snapshot of {low, high, trend} at moment of alert
    notification_id TEXT,                                -- Telegram message id
    UNIQUE (ticker, boundary, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts_fired(ticker);
CREATE INDEX IF NOT EXISTS idx_alerts_date   ON alerts_fired(signal_date);
CREATE INDEX IF NOT EXISTS idx_alerts_fired  ON alerts_fired(fired_at);


-- ============================================================================
-- OPERATIONAL TABLES — portfolio + recommendations (ported from SQLite)
-- ============================================================================
-- Existing SQLite items/signals tables are NOT migrated. They can be
-- regenerated by running parsers over the email lake.
-- ============================================================================

CREATE TABLE IF NOT EXISTS portfolio_positions (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE NOT NULL,
    account_number  TEXT,
    account_name    TEXT,
    symbol          TEXT NOT NULL,
    description     TEXT,
    quantity        NUMERIC(14,4),
    last_price      NUMERIC(14,4),
    current_value   NUMERIC(14,2),
    today_gl_dollar NUMERIC(14,2),
    today_gl_pct    NUMERIC(8,4),
    total_gl_dollar NUMERIC(14,2),
    total_gl_pct    NUMERIC(8,4),
    pct_of_account  NUMERIC(8,4),
    cost_basis      NUMERIC(14,2),
    avg_cost_basis  NUMERIC(14,4),
    account_type    TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_date, account_number, symbol)
);

CREATE INDEX IF NOT EXISTS idx_pos_date   ON portfolio_positions(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_pos_symbol ON portfolio_positions(symbol);
CREATE INDEX IF NOT EXISTS idx_pos_acct   ON portfolio_positions(account_number);


CREATE TABLE IF NOT EXISTS portfolio_transactions (
    id              BIGSERIAL PRIMARY KEY,
    run_date        DATE,
    account         TEXT,
    action          TEXT,
    symbol          TEXT,
    description     TEXT,
    security_type   TEXT,
    quantity        NUMERIC(14,4),
    price           NUMERIC(14,4),
    amount          NUMERIC(14,2),
    commission      NUMERIC(14,2),
    fees            NUMERIC(14,2),
    settlement_date DATE,
    source_file     TEXT,
    row_hash        TEXT UNIQUE,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_txn_symbol ON portfolio_transactions(symbol);
CREATE INDEX IF NOT EXISTS idx_txn_date   ON portfolio_transactions(run_date);
CREATE INDEX IF NOT EXISTS idx_txn_acct   ON portfolio_transactions(account);


CREATE TABLE IF NOT EXISTS trade_recommendations (
    id                  BIGSERIAL PRIMARY KEY,
    signal_email_id     TEXT REFERENCES hedgeye_emails_raw(message_id) ON DELETE SET NULL,
    ticker              TEXT NOT NULL,
    direction           TEXT,                            -- Long / Short
    conviction          TEXT,                            -- Best Idea / Adding / Monitor / Reducing / Remove
    account             TEXT,                            -- target account number
    action              TEXT,                            -- BUY / SELL / SHORT / COVER / SKIP
    recommended_dollars NUMERIC(14,2),
    recommended_shares  NUMERIC(14,4),
    reference_price     NUMERIC(14,4),
    current_shares      NUMERIC(14,4),
    reasoning           TEXT,
    status              TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','approved','rejected','executed','expired')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_recs_ticker ON trade_recommendations(ticker);
CREATE INDEX IF NOT EXISTS idx_recs_status ON trade_recommendations(status);
CREATE INDEX IF NOT EXISTS idx_recs_created ON trade_recommendations(created_at);


-- ============================================================================
-- END OF SCHEMA
-- ============================================================================
