-- 073: ticker_tags non-GICS exposure axis + geared flags.
--
-- WHY: GICS is equity-only. yfinance gives ETFs no sector, so ETFs/thematics/
-- country/duration/commodity funds land review=1 and CONC buckets them as
-- "untagged" — even though they carry instrument/subsector. Rotation can't
-- move capital into "inversely correlated" names it can't classify, and CONC
-- can't cluster commodity ETFs (the energy-lesson failure mode).
--
-- exposure       = the underlying KIND on a non-GICS axis. Values (operator
--                  vocabulary, extend freely):
--                    single-country | thematic | commodity-proxy |
--                    broad-market   | volatility | crypto-proxy   |
--                    fixed-income   | currency
--                  NULL = not yet classified (review pass).
-- inverse        = 1 if the fund is inverse/short exposure, else 0.
-- leverage_factor= geared multiple (2, 3, …); NULL/1 = unlevered. Orthogonal
--                  to exposure (a 2x oil fund is commodity-proxy + lev 2).
--
-- All three default to "unknown/none" so a backfill that only fills NULLs
-- never overwrites an operator-confirmed row.
ALTER TABLE ticker_tags ADD COLUMN IF NOT EXISTS exposure        TEXT;
ALTER TABLE ticker_tags ADD COLUMN IF NOT EXISTS inverse         SMALLINT DEFAULT 0;
ALTER TABLE ticker_tags ADD COLUMN IF NOT EXISTS leverage_factor NUMERIC;
