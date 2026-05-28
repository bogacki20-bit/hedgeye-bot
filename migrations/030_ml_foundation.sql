-- ============================================================================
-- Migration 030 — ML training foundation (2026-05-28).
--
-- Operator framing: Portfolio Solutions is the #1 ML training signal because
-- PS is Keith McCullough's ACTUAL model portfolio — every PS add / trim /
-- exit is Keith putting real conviction to work. To make this trainable, the
-- bot needs:
--
--   (1) A clean history of Quad-regime transitions and helpers to read the
--       active regime canonically (instead of every caller poking the env
--       vars or the bot_state table independently).
--   (2) Split monthly / quarterly Quad columns on every table that already
--       carries a `quad_regime` text field (alerts_fired, actions_log,
--       outcomes_log) so ML can slice trades by regime axis cleanly. Legacy
--       columns are PRESERVED — additive only.
--   (3) The same monthly / quarterly columns on the two PS tables
--       (hedgeye_portfolio_actions, hedgeye_portfolio_solutions) so the
--       ML can query "how did Keith trade OIH in Q2 vs Q3" directly.
--   (4) Populated rank_change_1w / rank_change_1m on every
--       hedgeye_portfolio_solutions row (currently NULL on all 2031 rows —
--       schema had the columns, parser never filled them). Backfill via
--       SQL window-function lookup against prior snapshots.
--   (5) A materialized view `keith_trades_with_context` joining
--       hedgeye_portfolio_actions × hedgeye_portfolio_solutions so ML
--       gets "Keith's literal trade + his rank for that ticker on that
--       day + the rank-change context" in one row per (date, ticker).
--
-- Idempotent, additive only. Re-running this migration is safe.
-- ============================================================================


-- ─────────────────────────── (1) quad_regime_history ──────────────────────

-- Canonical history of which Quad regime was active when. The bot's startup
-- (tools.quad_regime.sync_quad_regime_from_env) compares the current env-var
-- values against the most-recent row here; if they differ, a new row is
-- inserted. This gives ML a timeline of regime transitions for every alert
-- and outcome to be retroactively tagged against.
CREATE TABLE IF NOT EXISTS quad_regime_history (
    id              SERIAL PRIMARY KEY,
    effective_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    monthly_quad    TEXT NOT NULL,
    quarterly_quad  TEXT NOT NULL,
    global_quad     TEXT,                  -- optional combined label
    source          TEXT NOT NULL DEFAULT 'operator',  -- 'operator' | 'detected' | 'startup' | 'backfill'
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quad_history_effective
    ON quad_regime_history (effective_at DESC);


-- Seed with the operator's current regime so backfill has SOMETHING to
-- stamp against. Only fires when the table is empty — never overwrites a
-- real operator-set row. The values match what tools/doctrine.py is
-- defaulting to right now (the operator's stated Q2 monthly / Q3
-- quarterly per his earlier instructions); the seed is explicitly
-- labeled source='backfill' so downstream consumers know it's an
-- inferred baseline, not an operator action.
INSERT INTO quad_regime_history (monthly_quad, quarterly_quad, source, notes, effective_at)
SELECT 'Quad 2', 'Quad 3', 'backfill',
       'baseline seed from migration 030; operator can override via CURRENT_*_QUAD_OVERRIDE',
       '2026-01-01 00:00:00+00'   -- backdate so all historical rows resolve to a regime
WHERE NOT EXISTS (SELECT 1 FROM quad_regime_history);


-- ─────────── (2) Split monthly/quarterly columns — already-stamped tables ─

-- alerts_fired & actions_log already carry the legacy `quad_regime` text
-- column from migration 009. Add split monthly/quarterly so the ML query
-- surface is clean. New inserts (db_pg.record_alert,
-- tools/import_fidelity_history) populate both during the deprecation
-- window; downstream code can switch to the split columns at its leisure.
ALTER TABLE alerts_fired ADD COLUMN IF NOT EXISTS monthly_quad   TEXT;
ALTER TABLE alerts_fired ADD COLUMN IF NOT EXISTS quarterly_quad TEXT;

ALTER TABLE actions_log  ADD COLUMN IF NOT EXISTS monthly_quad   TEXT;
ALTER TABLE actions_log  ADD COLUMN IF NOT EXISTS quarterly_quad TEXT;

-- outcomes_log already has quad_regime_at_open / _at_close (migration 010).
-- Add the split-axis counterparts for ML training symmetry. Backfill is
-- handled by the existing tools/compute_outcomes.py re-run path.
ALTER TABLE outcomes_log ADD COLUMN IF NOT EXISTS monthly_quad_at_entry   TEXT;
ALTER TABLE outcomes_log ADD COLUMN IF NOT EXISTS quarterly_quad_at_entry TEXT;
ALTER TABLE outcomes_log ADD COLUMN IF NOT EXISTS monthly_quad_at_exit    TEXT;
ALTER TABLE outcomes_log ADD COLUMN IF NOT EXISTS quarterly_quad_at_exit  TEXT;


-- ─────────── (3) Quad regime columns on PS tables ────────────────────────

ALTER TABLE hedgeye_portfolio_actions   ADD COLUMN IF NOT EXISTS monthly_quad   TEXT;
ALTER TABLE hedgeye_portfolio_actions   ADD COLUMN IF NOT EXISTS quarterly_quad TEXT;

ALTER TABLE hedgeye_portfolio_solutions ADD COLUMN IF NOT EXISTS monthly_quad   TEXT;
ALTER TABLE hedgeye_portfolio_solutions ADD COLUMN IF NOT EXISTS quarterly_quad TEXT;

-- Backfill the PS tables using whichever quad_regime_history row was
-- effective on each (action_date / snapshot_date). The seed row above
-- covers everything back to 2026-01-01; operator can later insert
-- pre-dated rows for true historical regime transitions and re-run this
-- backfill clause.
UPDATE hedgeye_portfolio_actions a
SET monthly_quad   = h.monthly_quad,
    quarterly_quad = h.quarterly_quad
FROM (
    SELECT DISTINCT ON (action_date)
           a2.action_date,
           h2.monthly_quad,
           h2.quarterly_quad
      FROM hedgeye_portfolio_actions a2
      JOIN quad_regime_history h2 ON h2.effective_at <= a2.action_date::timestamp + interval '23 hours 59 minutes'
     ORDER BY a2.action_date, h2.effective_at DESC
) h
WHERE a.action_date = h.action_date
  AND (a.monthly_quad IS NULL OR a.quarterly_quad IS NULL);

UPDATE hedgeye_portfolio_solutions s
SET monthly_quad   = h.monthly_quad,
    quarterly_quad = h.quarterly_quad
FROM (
    SELECT DISTINCT ON (snapshot_date)
           s2.snapshot_date,
           h2.monthly_quad,
           h2.quarterly_quad
      FROM hedgeye_portfolio_solutions s2
      JOIN quad_regime_history h2 ON h2.effective_at <= s2.snapshot_date::timestamp + interval '23 hours 59 minutes'
     ORDER BY s2.snapshot_date, h2.effective_at DESC
) h
WHERE s.snapshot_date = h.snapshot_date
  AND (s.monthly_quad IS NULL OR s.quarterly_quad IS NULL);


-- ─────────── (4) Populate rank_change_1w / rank_change_1m ────────────────

-- Convention (matches tools/event_driven_alerts.py existing logic):
--   rank_change = previous_rank - current_rank
--   POSITIVE = ticker moved UP in the rank list (climbed)
--   NEGATIVE = ticker moved DOWN
--   NULL     = no prior snapshot found within the window (new addition,
--              gap in publication, etc.)
--
-- We compute against the most-recent snapshot at-or-before
-- (current - N days), not strictly the row N days ago, so a Hedgeye
-- publication gap (Memorial Day weekend, etc.) doesn't blank the
-- delta.
UPDATE hedgeye_portfolio_solutions h
SET rank_change_1w = (
    SELECT prev.rank - h.rank
      FROM hedgeye_portfolio_solutions prev
     WHERE prev.ticker = h.ticker
       AND prev.snapshot_date <= h.snapshot_date - interval '7 days'
     ORDER BY prev.snapshot_date DESC
     LIMIT 1
),
rank_change_1m = (
    SELECT prev.rank - h.rank
      FROM hedgeye_portfolio_solutions prev
     WHERE prev.ticker = h.ticker
       AND prev.snapshot_date <= h.snapshot_date - interval '30 days'
     ORDER BY prev.snapshot_date DESC
     LIMIT 1
)
WHERE h.rank_change_1w IS NULL OR h.rank_change_1m IS NULL;


-- ─────────── (5) keith_trades_with_context materialized view ─────────────

-- ML training oracle: every parsed Keith action × his concurrent rank in
-- the model portfolio × regime context. One row per (action_date, ticker,
-- action). LEFT JOIN so action rows without a same-day snapshot still
-- appear (e.g. EOD action where the snapshot ingest came later).
--
-- Dropped+recreated unconditionally so re-running migrations doesn't
-- inherit stale column shape from earlier prototypes.
DROP MATERIALIZED VIEW IF EXISTS keith_trades_with_context;

CREATE MATERIALIZED VIEW keith_trades_with_context AS
SELECT
    a.id                AS action_id,
    a.action_date,
    a.ticker,
    a.action,
    a.bps,
    a.commentary_raw,
    a.monthly_quad,
    a.quarterly_quad,
    s.rank              AS rank_in_portfolio_at_time,
    s.rank_change_1w,
    s.rank_change_1m,
    a.source_email_id,
    a.captured_at
  FROM hedgeye_portfolio_actions a
  LEFT JOIN hedgeye_portfolio_solutions s
    ON s.ticker = a.ticker
   AND s.snapshot_date = a.action_date;

CREATE INDEX idx_ktwc_ticker         ON keith_trades_with_context (ticker);
CREATE INDEX idx_ktwc_ticker_mquad   ON keith_trades_with_context (ticker, monthly_quad);
CREATE INDEX idx_ktwc_ticker_qquad   ON keith_trades_with_context (ticker, quarterly_quad);
CREATE INDEX idx_ktwc_action_date    ON keith_trades_with_context (action_date DESC);
CREATE INDEX idx_ktwc_action         ON keith_trades_with_context (action);

-- The view doesn't auto-refresh. parser_portfolio_solutions.refresh_view()
-- calls REFRESH MATERIALIZED VIEW on each successful PS parse, and the
-- nightly event-alerts launcher also refreshes as a belt-and-suspenders.
