-- Migration 034 — sg_levels: deterministic SpotGamma levels table.
--
-- SG was stripped from the live path 2026-05-24 because the prose
-- summary was confusing the LLM (citing SPY's $750 call wall on a BRENT
-- alert, etc). Re-introduced 2026-06-10 as DETERMINISTIC ONLY: Python
-- reads the latest row for a ticker, builds the level-anchored sentence,
-- and either appends it to the alert body verbatim (price_monitor) or
-- passes one pre-computed line to the LLM (decision_engine — never the
-- raw JSON, never the prose). SG refines terrain (entry/level/structure)
-- — Hedgeye RR trend decides direction.
--
-- Populated by:
--   - tape_15m capture (intra-session, every 15 min) — written by the
--     tape watcher (work order item 7). Also persists the prose report
--     into corpus_documents source='spotgamma_tape_15m' for ML.
--   - daily_ingest (existing daily SpotGamma email ingest path).
--   - manual (operator-pasted levels via /sg or similar bridge command).
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS sg_levels (
    id                BIGSERIAL PRIMARY KEY,
    ticker            TEXT       NOT NULL,
    captured_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    capture_type      TEXT       NOT NULL,
    gamma_flip        NUMERIC,
    call_wall         NUMERIC,
    put_wall          NUMERIC,
    hedge_wall        NUMERIC,
    key_gamma_strike  NUMERIC,
    raw               JSONB
);

CREATE INDEX IF NOT EXISTS idx_sg_levels_ticker_captured_at
    ON sg_levels (ticker, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_sg_levels_capture_type_captured_at
    ON sg_levels (capture_type, captured_at DESC);
