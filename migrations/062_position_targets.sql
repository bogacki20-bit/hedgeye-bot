-- 062_position_targets.sql — TRANCHE v2: operator-set position size targets.
--
-- fill% = current % of account / target % of account. Replaces buy-count
-- tranche inference (T1/3 notation dropped from REPORT same session — wrong
-- data is worse than missing data). Rows are IDENTITY FACTS: operator-set
-- via Telegram (TARGET ... -> CONFIRM TARGET), never inferred, never
-- LLM-written. Names without a row use tier DEFAULTS printed as such
-- (·dflt / ·dflt-etf), refined over time.

BEGIN;

CREATE TABLE IF NOT EXISTS position_targets (
    ticker      TEXT    NOT NULL,
    account     TEXT    NOT NULL CHECK (account IN ('IND', 'RIRA', 'ROTH')),
    target_pct  NUMERIC NOT NULL CHECK (target_pct > 0 AND target_pct <= 25),
    set_date    DATE    NOT NULL DEFAULT CURRENT_DATE,
    note        TEXT,
    PRIMARY KEY (ticker, account)
);

COMMIT;
