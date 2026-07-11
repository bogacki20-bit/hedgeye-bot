-- 060_report_rows.sql — stored REPORT snapshots (build queue item 4).
-- Every rendered report is a corpus row: the market as the bot described it
-- at that moment. EOD rows accumulate into the ML training set.

BEGIN;

CREATE TABLE IF NOT EXISTS report_rows (
    id          BIGSERIAL PRIMARY KEY,
    as_of       TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL DEFAULT 'on-demand',   -- on-demand / eod
    body        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_report_rows_asof ON report_rows (as_of);

COMMIT;
