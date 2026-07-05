-- 052_etfpro_bias.sql — persist ETF Pro long/short bias as a first-class column.
--
-- parser_etf_pro already parses each ETF's section (BULLISH -> long / BEARISH -> short)
-- into `bias`, but only ever stored it inside the free-text `description`
-- ("LONG | added … | recent …"). This adds a typed `bias` column so the source
-- registry / screener can screen ETF Pro's ACTUAL sided book ("SCREEN etf pro shorts"),
-- matching keiths. The parser now writes bias on every upsert (going forward).
--
-- Backfill is fully recoverable from the description prefix (100% coverage: 459 long /
-- 313 short of 772 rows). Idempotent — only fills NULLs.

ALTER TABLE hedgeye_etf_pro_ranges ADD COLUMN IF NOT EXISTS bias text;

UPDATE hedgeye_etf_pro_ranges
   SET bias = lower(split_part(description, ' ', 1))
 WHERE bias IS NULL
   AND upper(split_part(description, ' ', 1)) IN ('LONG', 'SHORT');
