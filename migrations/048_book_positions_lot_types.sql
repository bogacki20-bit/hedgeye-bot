-- 048_book_positions_lot_types.sql — preserve the Fidelity lot Type breakdown.
--
-- Fidelity lists a holding as multiple lot rows per account (Type = Cash / Margin
-- / Short). ingest_fidelity now collapses those into one position per
-- (snapshot_date, account_number, symbol) with summed quantity/value/cost, and
-- records which lot types made it up (e.g. 'Cash+Margin', 'Short') here — the
-- margin/cash split is decision-relevant and must not be discarded.

ALTER TABLE book_positions ADD COLUMN IF NOT EXISTS lot_types TEXT;
