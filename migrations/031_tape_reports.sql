-- ============================================================================
-- Migration 031 — spotgamma_tape_reports (intraday tape state for ML)
--
-- The scheduled `spotgamma-tape-watch` task captures the SpotGamma Tape Tool
-- every 15 min during market hours and POSTs the structured data to the bot's
-- /api/tape_report endpoint (see api.py). That endpoint writes TWO places in a
-- single transaction:
--   1. corpus_documents  (source='spotgamma_tape')  — RAG / full-text corpus
--   2. spotgamma_tape_reports (this table)          — typed columns for ML
--
-- Apply via apply_migration.py (uses DATABASE_PUBLIC_URL / DATABASE_URL).
-- All statements idempotent — safe to re-run.
-- ============================================================================

-- ── Structured tape table ───────────────────────────────────────────────────
-- Schema mirrors the one documented in the spotgamma-tape-watch SKILL, plus
-- the index levels (spx/ndx/vix) carried in the POST body. captured_at is the
-- natural idempotency key — one row per 15-min capture; a re-send of the same
-- captured_at updates the existing row instead of duplicating.
CREATE TABLE IF NOT EXISTS spotgamma_tape_reports (
    id                      SERIAL PRIMARY KEY,
    captured_at             TIMESTAMPTZ NOT NULL,

    -- Index spot levels at capture time (from POST body; nullable).
    spx                     DOUBLE PRECISION,
    ndx                     DOUBLE PRECISION,
    vix                     DOUBLE PRECISION,

    -- Top 5 options volume                  [{"ticker":"SPY","volume":"16M"}, ...]
    top_volume_json         JSONB,
    -- Top 5 daily gamma notional            [{"ticker":"SPY","gamma":"51B"}, ...]
    top_gamma_notional_json JSONB,
    -- Top 5 daily movers                    [{"ticker":"AZUL","last":8.37,"close":0.51,"change_pct":-93.91}, ...]
    top_movers_json         JSONB,
    -- Largest 5 daily trades (institutional) [{"ticker":"SMH","premium_usd":83000000,"expiry":"2026-09-18","strike":400,"type":"Call"}, ...]
    largest_trades_json     JSONB,
    -- Live flow tape sample                 [{"time":"...","symbol":"...","side":"...","cp":"C","strike":...,"volume":...,"oi":...}, ...]
    live_flow_sample_json   JSONB,

    -- Operator-relevant takeaways
    bullish_concentration   TEXT,
    bearish_concentration   TEXT,
    decision_note           TEXT,

    raw_report_markdown     TEXT,
    screenshot_path         TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- If the table pre-existed from a manual SKILL run (older shape), make sure the
-- index-level columns exist.
ALTER TABLE spotgamma_tape_reports ADD COLUMN IF NOT EXISTS spx DOUBLE PRECISION;
ALTER TABLE spotgamma_tape_reports ADD COLUMN IF NOT EXISTS ndx DOUBLE PRECISION;
ALTER TABLE spotgamma_tape_reports ADD COLUMN IF NOT EXISTS vix DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_spotgamma_tape_reports_captured_at
    ON spotgamma_tape_reports (captured_at DESC);

-- Idempotency key: re-POSTing the same captured_at must not duplicate.
-- A plain UNIQUE constraint can't be added IF NOT EXISTS, so use a unique index.
CREATE UNIQUE INDEX IF NOT EXISTS spotgamma_tape_reports_captured_at_uniq
    ON spotgamma_tape_reports (captured_at);

-- ── corpus_documents idempotency ────────────────────────────────────────────
-- NOTE (intentional deviation from the task spec): we do NOT add a global
-- UNIQUE(source, source_date) to corpus_documents.
--   * That constraint would FAIL against existing data — other sources legitimately
--     have many rows per (source, source_date) (e.g. macro_show_deck has one row per
--     slide via page_or_segment; spotgamma per-date has many source_refs).
--   * It would also collapse intraday tape captures to ONE row per day, destroying
--     the 15-min granularity this whole feature exists to capture.
-- Instead, idempotency is achieved through the EXISTING unique index
--   corpus_documents_unique (source, COALESCE(source_ref,''), source_date,
--                            COALESCE(page_or_segment,-1))
-- by writing the full capture timestamp into source_ref. Two different captures
-- have different source_ref → two rows; a re-send of the same captured_at hits
-- ON CONFLICT and updates in place. No schema change to corpus_documents needed.
