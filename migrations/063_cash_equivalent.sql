-- 063_cash_equivalent.sql — v4.1 FIX 2: cash-parking funds are dry powder,
-- not positions. Flag lives on ticker_tags (operator-confirmed identity
-- fact; write path = TARGET CASHEQ <tkr> -> CONFIRM TARGET, or the gated
-- apply script). Effect: excluded from BOOK exposure/fills/CONC; rolled
-- into the CASH line as 'parked'. Seeding (BUXX) happens in _apply_063
-- --commit, not here — data writes stay behind the CONFIRM gate.

BEGIN;

ALTER TABLE ticker_tags ADD COLUMN IF NOT EXISTS cash_equivalent SMALLINT DEFAULT 0;

COMMIT;
