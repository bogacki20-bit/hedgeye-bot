-- ============================================================================
-- Migration 084 — ts_export_log: TrendSpider custom-symbol export ledger
-- (2026-09-04, TRENDSPIDER_ML_ROUND1_SPEC_v1 Step 1)
--
-- One row per (symbol, source row) exported to TrendSpider custom symbols.
-- Corpus-first: trendspider_export.py stages rows here BEFORE uploading;
-- uploaded_at/http_status record what actually landed.
--
--   symbol      TrendSpider custom symbol incl. leading '#' (e.g. #MFR_SPY_LO)
--   source_key  dedupe identity of the SOURCE row (operator decision 5,
--               2026-09-04): ISO snapshot_date for daily tables,
--               effective_at::text for quad_regime_history. A (symbol,
--               source_key) pair is exported at most once — never re-exported
--               after upload, even if the source row's write-timestamp is
--               later bumped by an upsert.
--   known_at    the timestamp the stored source row was written by its
--               producing job (mfr fetched_at / volume+rs+div computed_at /
--               quad effective_at) — NEVER the market date. NOTE (decision
--               5): source tables bump their write-timestamp on re-fetch
--               upserts, so known_at can be LATER than first-known. That is
--               the safe direction for backtests (never earlier than truly
--               available).
--   value       the exported numeric value, exactly as staged for the CSV.
--   batch_id    upload batch tag; uploaded_at/http_status NULL until a 2xx.
--               http_status is recorded on failed attempts too (uploaded_at
--               stays NULL so the row is retried next run).
--
-- Append-only by design; no destructive path. Idempotent — safe to re-run.
-- Apply via:  py apply_migration.py migrations/084_ts_export_log.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS ts_export_log (
    id           BIGSERIAL PRIMARY KEY,
    symbol       TEXT NOT NULL,
    source_key   TEXT NOT NULL,
    known_at     TIMESTAMPTZ NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    batch_id     TEXT,
    uploaded_at  TIMESTAMPTZ,
    http_status  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, known_at),
    UNIQUE (symbol, source_key)
);

CREATE INDEX IF NOT EXISTS idx_ts_export_pending
    ON ts_export_log (symbol, uploaded_at) WHERE uploaded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_ts_export_symbol_key
    ON ts_export_log (symbol, source_key DESC);
