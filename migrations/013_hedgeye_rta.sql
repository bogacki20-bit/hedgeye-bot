-- ============================================================================
-- Migration 013 — typed table for Real-Time Alerts (RTAs)
--
-- RTA emails are Keith/analyst single-name trade signals. One email == one
-- signal. Subject grammar:
--   **Real-Time Alert: [<Analyst> ]<SignalType> Signal (<note>): <Company>[ (<TICKER>)] [-KM]
-- Body signal line:
--   "BUY SIGNAL CAT $883.46"  /  "SELL SIGNAL - COVERING SHORT THC $197.90"
-- plus a "05/15/2026 12:03 PM EDT" timestamp and an app.hedgeye.com/feed_items/<id> link.
--
-- One email is exactly one RTA, so source_email_id is the natural key; the
-- (signal_date, ticker, source_email_id) UNIQUE keeps re-parses idempotent
-- while still allowing multiple RTAs on the same ticker/day from different emails.
-- ============================================================================

CREATE TABLE IF NOT EXISTS hedgeye_rta (
    id              BIGSERIAL PRIMARY KEY,
    signal_date     DATE NOT NULL,
    signal_ts       TIMESTAMPTZ,                 -- parsed from body "MM/DD/YYYY HH:MM AM EDT"
    ticker          TEXT NOT NULL,
    company_name    TEXT,                        -- from subject tail, when present
    analyst         TEXT,                        -- "McGough"/"Macro"/... ; NULL when desk-anon
    signal_type     TEXT,                        -- normalized: buy/sell/buy-some/sell-some/cover-some/cover
    action          TEXT,                        -- BUY/SELL from body signal line
    is_cover_short  BOOLEAN NOT NULL DEFAULT FALSE,  -- body "COVERING SHORT"
    side            TEXT,                        -- derived long/short
    price           NUMERIC(14,4),               -- price at signal from body
    km_flag         BOOLEAN NOT NULL DEFAULT FALSE,  -- "-KM" in subject
    note            TEXT,                        -- subject parenthetical
    feed_item_id    BIGINT,                      -- app.hedgeye.com/feed_items/<id>
    source_email_id TEXT REFERENCES hedgeye_emails_raw(message_id)
                         ON DELETE SET NULL,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (signal_date, ticker, source_email_id)
);

CREATE INDEX IF NOT EXISTS idx_rta_date    ON hedgeye_rta(signal_date);
CREATE INDEX IF NOT EXISTS idx_rta_ticker  ON hedgeye_rta(ticker);
CREATE INDEX IF NOT EXISTS idx_rta_analyst ON hedgeye_rta(analyst);
CREATE INDEX IF NOT EXISTS idx_rta_feed    ON hedgeye_rta(feed_item_id);
