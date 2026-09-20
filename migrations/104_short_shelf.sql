-- 104_short_shelf.sql — the WATCH SHELF (operator + desk audit, 9/20).
--
-- The short doctrine recycles covered shorts on the next qualifying
-- bounce, but the recycle half ran on the operator's memory. One row per
-- (ticker, cover_date, account): what was covered, at what, why, and what
-- happened next. Rows stay live until re-shorted or the name loses its
-- short thesis — expiries are logged with a reason, never silently
-- dropped. Stats over these rows answer the shelf's founding question:
-- do second rentals perform better or worse than the first?

BEGIN;

CREATE TABLE IF NOT EXISTS short_shelf (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT        NOT NULL,
    account_number  TEXT        NOT NULL,
    cover_date      DATE        NOT NULL,
    cover_price     NUMERIC,             -- qty-weighted avg of the day's covers
    qty_covered     NUMERIC     NOT NULL,
    partial         BOOLEAN     NOT NULL DEFAULT FALSE,  -- position still open after
    reason          TEXT,                -- put-wall | clock | trend-flip | monitor-drop | NULL=unclassified
    realized_pl     NUMERIC,             -- approx: lifetime avg short price - cover price, x qty
    status          TEXT        NOT NULL DEFAULT 'live', -- live | re-entered | expired
    reentered_at    DATE,
    reentry_price   NUMERIC,
    expired_at      DATE,
    expiry_reason   TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, account_number, cover_date)
);

CREATE INDEX IF NOT EXISTS ix_shelf_status ON short_shelf (status);

COMMIT;
