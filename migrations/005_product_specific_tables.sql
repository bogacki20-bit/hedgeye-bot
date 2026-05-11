-- ============================================================================
-- Migration 005 — typed tables for Portfolio Solutions, Signal Strength,
-- Investing Ideas. Plus a free-form actions table for Keith's commentary
-- ("I sold 100bps of BUXX, OIH...") which is the mimic-Keith signal.
--
-- Each table follows the pattern: snapshot_date + ticker as composite PK so
-- daily refreshes are idempotent.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Portfolio Solutions — Keith's daily ETF re-rank
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hedgeye_portfolio_solutions (
    snapshot_date   DATE NOT NULL,
    rank            INT NOT NULL,
    ticker          TEXT NOT NULL,
    rank_change_1w  INT,                    -- e.g. +11 for OIH on 5/8
    rank_change_1m  INT,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_ps_ticker ON hedgeye_portfolio_solutions(ticker);
CREATE INDEX IF NOT EXISTS idx_ps_date   ON hedgeye_portfolio_solutions(snapshot_date);


-- ----------------------------------------------------------------------------
-- Portfolio actions — extracted from Keith's commentary blocks in PS emails.
-- "In the PA today, I sold 100bps of BUXX, OIH, and STIP. Sold 50bps EQRR, SOYB."
-- This is the mimic-Keith signal source.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hedgeye_portfolio_actions (
    id              BIGSERIAL PRIMARY KEY,
    action_date     DATE NOT NULL,           -- date Keith took the action
    ticker          TEXT NOT NULL,
    action          TEXT NOT NULL,           -- 'sold' / 'bought' / 'added' / 'trimmed'
    bps             INT,                     -- 50 / 100 / etc; null if unparsed
    commentary_raw  TEXT,                    -- full sentence containing this action
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_email_id, ticker, action)
);

CREATE INDEX IF NOT EXISTS idx_pact_ticker ON hedgeye_portfolio_actions(ticker);
CREATE INDEX IF NOT EXISTS idx_pact_date   ON hedgeye_portfolio_actions(action_date);


-- ----------------------------------------------------------------------------
-- Signal Strength — Hedgeye's curated single-stock watchlist (~90 names)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hedgeye_signal_strength (
    snapshot_date   DATE NOT NULL,
    ticker          TEXT NOT NULL,
    is_added_today  BOOLEAN NOT NULL DEFAULT FALSE,
    is_removed_today BOOLEAN NOT NULL DEFAULT FALSE,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_ss_ticker ON hedgeye_signal_strength(ticker);
CREATE INDEX IF NOT EXISTS idx_ss_date   ON hedgeye_signal_strength(snapshot_date);


-- ----------------------------------------------------------------------------
-- Investing Ideas — Top 21 Long Ideas leaderboard (analyst-driven longs)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hedgeye_investing_ideas (
    snapshot_date   DATE NOT NULL,
    rank            INT NOT NULL,
    ticker          TEXT NOT NULL,
    analyst         TEXT,
    pct_change      NUMERIC(8,2),
    date_added      DATE,
    date_removed    DATE,
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_ii_ticker  ON hedgeye_investing_ideas(ticker);
CREATE INDEX IF NOT EXISTS idx_ii_date    ON hedgeye_investing_ideas(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ii_analyst ON hedgeye_investing_ideas(analyst);
