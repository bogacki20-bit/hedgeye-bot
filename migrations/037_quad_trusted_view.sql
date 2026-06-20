-- ============================================================================
-- Migration 037 — enforce quad quarantine in keith_trades_with_context
--
-- keith_trades_with_context (migration 030) is the per-quad / ML-training surface
-- ("how did Keith trade X in Quad N"). It exposed monthly_quad / quarterly_quad for
-- EVERY action row, so the 636 rows quarantined by migration 036 (quad_trusted=false,
-- off-by-one quad) still flowed through to per-quad readers — the flag protected
-- nothing downstream. This rebuilds the view to (a) filter WHERE a.quad_trusted=true
-- and (b) carry a.quad_trusted through so future readers can see the flag.
--
-- A materialized view can't be CREATE OR REPLACE'd, so DROP + CREATE (repopulates
-- immediately) and recreate indexes. No base-table data changed; nothing deleted
-- or re-stamped. monitor_context and all other quad paths untouched.
--
-- Apply via apply_migration.py. All statements idempotent — safe to re-run.
-- ============================================================================

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
    a.quad_trusted,                       -- carried through so readers can see the flag
    s.rank              AS rank_in_portfolio_at_time,
    s.rank_change_1w,
    s.rank_change_1m,
    a.source_email_id,
    a.captured_at
  FROM hedgeye_portfolio_actions a
  LEFT JOIN hedgeye_portfolio_solutions s
    ON s.ticker = a.ticker
   AND s.snapshot_date = a.action_date
 WHERE a.quad_trusted = true;            -- the wall: untrusted off-by-one rows excluded

CREATE INDEX idx_ktwc_ticker         ON keith_trades_with_context (ticker);
CREATE INDEX idx_ktwc_ticker_mquad   ON keith_trades_with_context (ticker, monthly_quad);
CREATE INDEX idx_ktwc_ticker_qquad   ON keith_trades_with_context (ticker, quarterly_quad);
CREATE INDEX idx_ktwc_action_date    ON keith_trades_with_context (action_date DESC);
CREATE INDEX idx_ktwc_action         ON keith_trades_with_context (action);
