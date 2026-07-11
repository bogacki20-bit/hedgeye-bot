-- 055_rta_closes.sql — same-day alert suppression on Hedgeye position-close RTAs.
--
-- Operator rule (2026-07-11): a CLOSE-type Real-Time Alert (signal_type
-- 'sell' or 'cover' — NOT the -SOME gradations, which are trims) removes the
-- name from the bot's alert universes for THAT calendar day. From the next
-- day the normal publication-refresh cycle governs, so nothing is ever
-- permanently muted by an RTA (enroll-never-remove stays intact for MFR).
--
-- Consumers: tools.active_slice.polling_universe() and
-- price_monitor.get_alert_ticker_universe() subtract today's rows.

BEGIN;

CREATE TABLE IF NOT EXISTS rta_position_closes (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    closed_on       DATE NOT NULL,
    signal_type     TEXT,
    source_email_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, closed_on)
);

CREATE INDEX IF NOT EXISTS ix_rta_closes_day ON rta_position_closes (closed_on);

COMMIT;
