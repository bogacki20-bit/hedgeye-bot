-- 058_ss_flow_events.sql — Signal Strength roster add/drop events with the
-- market structure FROZEN at event date (build queue item 3).
--
-- Operator frame: SS roster churn = the single-stock expression of the quad;
-- adds are "what works now". Same pattern as ps_flow_events (056): rows are
-- daily facts, written once; vol regime joins by date via vol_regime_daily;
-- quad stamps NULL before the clean-labeling start (2026-07-06).
-- Seed rows (the initial 80-name baseline) are NOT events and are excluded.

BEGIN;

CREATE TABLE IF NOT EXISTS ss_flow_events (
    id              BIGSERIAL PRIMARY KEY,
    event_date      DATE NOT NULL,
    ticker          TEXT NOT NULL,
    event           TEXT NOT NULL CHECK (event IN ('add', 'drop')),
    src             TEXT,               -- delta / anchor
    quad_monthly    TEXT,
    quad_quarterly  TEXT,
    price           NUMERIC,
    range_pos       NUMERIC,
    trend_signal    TEXT,
    momentum_signal TEXT,
    hurst           NUMERIC,
    iv              NUMERIC,
    rv              NUMERIC,
    ivpd            NUMERIC,
    lt_pos          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_date, ticker, event)
);

CREATE INDEX IF NOT EXISTS ix_ssflow_ticker ON ss_flow_events (ticker);
CREATE INDEX IF NOT EXISTS ix_ssflow_date   ON ss_flow_events (event_date);

COMMIT;
