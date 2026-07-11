-- 059_book_alerts.sql — dedup ledger for book-management alerts (queue 5).
-- One alert per (ticker, type) per day; append-only.

BEGIN;

CREATE TABLE IF NOT EXISTS book_alerts_fired (
    id          BIGSERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    alert_type  TEXT NOT NULL,          -- dip / rip / trend_flip
    fired_on    DATE NOT NULL,
    details     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, alert_type, fired_on)
);

COMMIT;
