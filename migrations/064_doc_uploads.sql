-- 064_doc_uploads.sql — Telegram document ingest (sprint P2, 2026-07-11).
--
-- Operator downloads a report on the phone and sends the FILE to the bot;
-- the bot extracts text, classifies it (founders_note_am/pm · flow_patrol ·
-- equity_hub · tier1alpha · other), and stores it here. This table is the
-- staging corpus for the future RAG layer. The 5K equity-scanner scrape is
-- DEAD (2 weeks stuck, operator-killed) — Equity Hub arrives as uploads.
-- The send itself is the operator action; ingest replies a loud summary
-- (kind, chars, note_date or 'undated — a fact without a date').

BEGIN;

CREATE TABLE IF NOT EXISTS doc_uploads (
    id           BIGSERIAL   PRIMARY KEY,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    source       TEXT        NOT NULL DEFAULT 'telegram',
    file_name    TEXT,
    kind         TEXT        NOT NULL,
    note_date    DATE,                      -- parsed from name/content; NULL is loud
    char_count   INTEGER,
    content_text TEXT,
    meta         JSONB
);

CREATE INDEX IF NOT EXISTS ix_docup_kind ON doc_uploads (kind, note_date);
CREATE INDEX IF NOT EXISTS ix_docup_at   ON doc_uploads (uploaded_at);

COMMIT;
