-- Investing Ideas emails have two ticker sources:
--   1. "DATES ADDED" prose listing the active 21 long ideas (no rank)
--   2. The "#N TICKER ANALYST PCT DATE" ranked table for closed positions
-- Active longs from (1) don't have a rank, so allow NULL.

ALTER TABLE hedgeye_investing_ideas
    ALTER COLUMN rank DROP NOT NULL;
