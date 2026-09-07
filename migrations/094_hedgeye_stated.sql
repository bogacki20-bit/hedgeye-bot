-- 094_hedgeye_stated.sql — roadmap §2 step 2: Hedgeye's stated view,
-- back-parsed from the mail archive (operator brief 2026-09-07).
--
-- hedgeye_quad_stated: every stated-Quad observation (revisions included —
-- the nowcast trajectory is the point). known_at = the email Date header.
-- inflation_nowcast: the Monthly Inflation Nowcast series (stated I-axis).
-- hedgeye_risk_ranges gains source_uid: archive-backfill provenance
-- (mail-archive folder/uid; live-parsed rows keep source_email_id).
-- quad_nowcast_daily: latest stated Quad as-of each trading day,
-- forward-filled by the parse job (a materialized table, not a view — the
-- forward-fill is procedural).
--
-- Apply via:  py apply_migration.py migrations/094_hedgeye_stated.sql

CREATE TABLE IF NOT EXISTS hedgeye_quad_stated (
    note_date  date NOT NULL,
    product    text NOT NULL,
    scope      text NOT NULL CHECK (scope IN ('monthly', 'quarterly')),
    period     text NOT NULL,          -- '3Q26' or '2026-08'
    quad       integer NOT NULL CHECK (quad BETWEEN 1 AND 4),
    gdp_est    numeric,                -- GIP-table rows only
    cpi_est    numeric,
    source_uid text NOT NULL,
    snippet    text NOT NULL,
    known_at   timestamptz NOT NULL,   -- the email Date header
    parsed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (note_date, product, scope, period, quad)
);

CREATE TABLE IF NOT EXISTS inflation_nowcast (
    note_date   date NOT NULL,
    month       date NOT NULL,         -- first of the month estimated
    cpi_yoy_est numeric NOT NULL,
    scenario    text NOT NULL DEFAULT 'base'
                CHECK (scenario IN ('base', 'upside', 'downside')),
    source_uid  text NOT NULL,
    known_at    timestamptz NOT NULL,
    PRIMARY KEY (note_date, month, scenario)
);

ALTER TABLE hedgeye_risk_ranges ADD COLUMN IF NOT EXISTS source_uid text;

CREATE TABLE IF NOT EXISTS quad_nowcast_daily (
    date             date PRIMARY KEY,
    monthly_quad     integer,
    quarterly_quad   integer,
    source_note_date date
);
