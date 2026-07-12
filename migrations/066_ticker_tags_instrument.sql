-- 066: ticker_tags.instrument — what kind of thing a ticker IS.
-- Values: 'etf' | 'stock' | 'fund' (mutual/money-market) | 'future' |
--         'index' | 'currency' | 'crypto'. NULL = not yet confirmed.
-- Replaces position_targets' description-regex guessing as the ground truth
-- for default-tier routing (identity fact, operator-confirmed on write).
ALTER TABLE ticker_tags ADD COLUMN IF NOT EXISTS instrument TEXT;
