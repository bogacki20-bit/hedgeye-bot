-- Migration 012 — alerts_fired.prompt_context_full
--
-- Stores the COMPLETE context that fed the LLM for a decision as
-- structured JSON: { static_framework, per_ticker_static, dynamic,
-- model, decided_at }. When ML training runs, every alert row carries
-- the full feature vector that produced the decision — not a
-- thinned-down summary. Token cost is cheap; data quality is expensive.
--
-- Idempotent. Additive only.

ALTER TABLE alerts_fired
    ADD COLUMN IF NOT EXISTS prompt_context_full JSONB;
