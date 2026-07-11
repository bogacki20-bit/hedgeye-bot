-- 056_ps_flow_events.sql — Portfolio Solutions add/drop events with the
-- market structure FROZEN at event time (trade-like-Keith corpus).
--
-- Operator doctrine: Keith's adds/removes are the LABELS; the structure at
-- the moment he acts is the FEATURES. Append-only; rows are never updated.
-- Stamps come from the event date's own mfr_snapshots row and
-- quad_regime_history — a fact without a date isn't a fact, so missing
-- history stays NULL (and the writer reports how many stamps were NULL).

BEGIN;

CREATE TABLE IF NOT EXISTS ps_flow_events (
    id              BIGSERIAL PRIMARY KEY,
    event_date      DATE NOT NULL,
    ticker          TEXT NOT NULL,
    event           TEXT NOT NULL CHECK (event IN ('add', 'drop')),
    rank            INT,                -- rank on the add-side snapshot
    -- frozen regime stamp
    quad_monthly    TEXT,
    quad_quarterly  TEXT,
    -- frozen structure stamp (from mfr_snapshots ON event_date, else NULL)
    price           NUMERIC,
    range_pos       NUMERIC,            -- (price-low)/(high-low)
    trend_signal    TEXT,
    momentum_signal TEXT,
    hurst           NUMERIC,
    iv              NUMERIC,
    rv              NUMERIC,
    ivpd            NUMERIC,
    lt_pos          TEXT,               -- above / in / below the MFR cloud
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_date, ticker, event)
);

CREATE INDEX IF NOT EXISTS ix_psflow_ticker ON ps_flow_events (ticker);
CREATE INDEX IF NOT EXISTS ix_psflow_date   ON ps_flow_events (event_date);

COMMIT;
