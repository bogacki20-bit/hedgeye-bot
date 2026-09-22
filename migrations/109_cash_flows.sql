-- 109_cash_flows.sql — non-trade cash flows (operator 9/22).
--
-- The Individual account doubles as the checking account, and the trade
-- importer DELIBERATELY skips non-trade rows — so ~$4K/week of spending
-- was invisible to every reconciliation and read as 'missing money'.
-- One row per non-trade cash event from the History_for_Account export:
-- deposits, withdrawals, debit cards, checks, margin interest, dividends.
-- Internal net-zero pairs (short-vs-margin MTM, journals) are excluded.

BEGIN;

CREATE TABLE IF NOT EXISTS cash_flows (
    id             BIGSERIAL PRIMARY KEY,
    account_number TEXT NOT NULL,
    flow_date      DATE NOT NULL,
    kind           TEXT NOT NULL,   -- deposit|withdrawal|debit_card|check|margin_interest|dividend|fee|other
    amount         NUMERIC NOT NULL, -- signed cash impact
    description    TEXT,
    pending        BOOLEAN NOT NULL DEFAULT FALSE,
    row_hash       TEXT UNIQUE NOT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_cashflows_date ON cash_flows (flow_date);

COMMIT;
