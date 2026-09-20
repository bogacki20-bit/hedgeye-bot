-- 106_sector_monitor.sql — per-sector Position Monitor state (operator 9/20:
-- "there should be a position monitor for each retail and financials").
--
-- Two Sunday email families carry monitor MOVES as text:
--   "Week of X: Position Monitors Update"  — all 15 sectors' changes
--   "Weekly Position Monitor | N Changes"  — Financials Pro's own monitor
-- Each move states the ticker's NEW tier, so a carry-forward table rebuilt
-- from every change email yields the current sector monitors without OCR
-- of the image-only lists. hedgeye_sector_monitor_events is append-only
-- (the audit trail); v-style current state = latest event per ticker.

BEGIN;

CREATE TABLE IF NOT EXISTS hedgeye_sector_monitor_events (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    sector          TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    tier            TEXT NOT NULL,   -- top_idea|active|bench|best_idea|removed
    side            TEXT,            -- long | short | NULL (removed w/o side)
    verb            TEXT,            -- moving|adding|removing|moved
    raw             TEXT,
    source_email_id TEXT,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (signal_date, sector, ticker, source_email_id)
);

CREATE INDEX IF NOT EXISTS ix_secmon_tkr ON hedgeye_sector_monitor_events (ticker, signal_date);

COMMIT;
