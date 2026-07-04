-- 049_bucket_history.sql — Position Monitor tier-transition history (append-only).
--
-- One row per bucket CHANGE for a ticker (incl. first sighting and removal, where
-- bucket='removed'). quad_monthly / quad_quarterly are FROZEN from the current
-- stored quad (hedgeye_quad) at write time — never joined retroactively; NULL when
-- the quad is unset. Append-only: no updates, no deletes. Written by
-- tools/bucket_history.record_bucket_change (Python owns the write). No backfill —
-- the clean corpus starts Mon 2026-07-07 with the first QUAD: 4/4 confirm.

CREATE TABLE IF NOT EXISTS bucket_history (
    id              BIGSERIAL   PRIMARY KEY,
    ticker          TEXT        NOT NULL,
    bucket          TEXT        NOT NULL,   -- active_long / top_idea_short / long_bench / 'removed'
    effective_date  DATE        NOT NULL,
    quad_monthly    TEXT,                   -- frozen at write time; NULL if quad unset
    quad_quarterly  TEXT,
    source_email_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_bucket_history_ticker_date ON bucket_history (ticker, effective_date);
