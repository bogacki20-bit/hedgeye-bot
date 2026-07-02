-- 046_book_positions_quantity_nullable.sql
--
-- Cash-sweep positions (money-market balances like SPAXX**/CORE**) have a dollar
-- value but no share quantity, so the parser correctly emits NULL quantity for
-- them. Migration 045 declared quantity NOT NULL, which rejected those rows. Relax
-- it: quantity is legitimately NULL for asset_class='cash'. Equity/option rows
-- still carry a real quantity from the parser.

ALTER TABLE book_positions ALTER COLUMN quantity DROP NOT NULL;
