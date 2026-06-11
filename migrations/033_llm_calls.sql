-- Migration 033 — llm_calls: per-Anthropic-call cost ledger.
--
-- Wraps both decision_engine call sites (decide_notifier / Haiku and the
-- preserved-but-unreachable _decide_legacy / Sonnet path) so the operator
-- can answer 'how much is this bot costing me today' with a SQL query
-- against one table instead of grepping logs.
--
-- Cost model: est_cost_usd is computed Python-side from per-model rates
-- (see decision_engine._llm_cost_estimate) and stamped at insert time —
-- if the API publishes a price change we update the rate table and
-- backfill via UPDATE … FROM llm_pricing rather than recomputing every
-- read. Cached input tokens billed at the cache-read rate (currently
-- ~10x cheaper than uncached input).
--
-- Daily check:
--   SELECT called_at::date, caller, COUNT(*),
--          SUM(input_tokens), SUM(output_tokens),
--          SUM(cache_read_tokens), SUM(est_cost_usd)
--     FROM llm_calls
--    WHERE called_at >= NOW() - interval '7 days'
--    GROUP BY 1, 2
--    ORDER BY 1 DESC, 2;
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS llm_calls (
    id                 BIGSERIAL PRIMARY KEY,
    called_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model              TEXT        NOT NULL,
    caller             TEXT        NOT NULL,
    ticker             TEXT,
    input_tokens       INTEGER     NOT NULL DEFAULT 0,
    output_tokens      INTEGER     NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER     NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER  NOT NULL DEFAULT 0,
    est_cost_usd       NUMERIC(10, 6) NOT NULL DEFAULT 0,
    -- Optional one-line debug context: signal_origin, error, etc.
    notes              TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_called_at
    ON llm_calls (called_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_caller_called_at
    ON llm_calls (caller, called_at DESC);
