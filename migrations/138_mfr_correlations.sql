-- 138: MFR correlation reads, nightly (Kris 2026-11-15 "Yes, add it"). Tracking only, not on the card.
-- Source: MyFractalRange MCP, read by tools/mfr_correlations.py with MFR_MCP_TOKEN (no ticker cost).
BEGIN;
-- AVG_SECTOR_CORR on SPY: mean correlation of the 11 sector SPDRs to SPY. High = market moves as one block.
CREATE TABLE IF NOT EXISTS mfr_sector_corr (
    bar_date    date PRIMARY KEY,
    avg10       numeric,
    avg21       numeric,
    fetched_at  timestamptz DEFAULT now()
);
-- mfr_get_today pair correlations (GLD/SPY, SPY/UUP, GLD/UUP, SPY/TLT): 10 and 21 day, MFR's trend word.
CREATE TABLE IF NOT EXISTS mfr_pair_corr (
    as_of       date NOT NULL,
    a           text NOT NULL,
    b           text NOT NULL,
    c10         numeric,
    c21         numeric,
    trend       text,
    fetched_at  timestamptz DEFAULT now(),
    PRIMARY KEY (as_of, a, b)
);
-- Each book name vs SPY / UUP / TLT / GLD / USO, 30 and 90 trading days. market_value from book_positions.
CREATE TABLE IF NOT EXISTS mfr_book_corr (
    as_of        date NOT NULL,
    ticker       text NOT NULL,
    book_date    date,
    market_value numeric,
    spy_30 numeric, spy_90 numeric,
    uup_30 numeric, uup_90 numeric,
    tlt_30 numeric, tlt_90 numeric,
    gld_30 numeric, gld_90 numeric,
    uso_30 numeric, uso_90 numeric,
    fetched_at   timestamptz DEFAULT now(),
    PRIMARY KEY (as_of, ticker)
);
-- Every pair among the book's 15 largest names (by |market value|), 30 and 90 day windows.
CREATE TABLE IF NOT EXISTS mfr_book_pairs (
    as_of       date NOT NULL,
    win         smallint NOT NULL,
    a           text NOT NULL,
    b           text NOT NULL,
    corr        numeric,
    fetched_at  timestamptz DEFAULT now(),
    PRIMARY KEY (as_of, win, a, b)
);
-- Book correlation weighted by market value (sum(mv*corr)/sum|mv|), one row per day.
CREATE OR REPLACE VIEW v_mfr_book_corr_summary AS
SELECT as_of,
       count(*) AS names,
       round(sum(market_value * spy_30) / nullif(sum(abs(market_value)) FILTER (WHERE spy_30 IS NOT NULL), 0), 3) AS spy_30,
       round(sum(market_value * spy_90) / nullif(sum(abs(market_value)) FILTER (WHERE spy_90 IS NOT NULL), 0), 3) AS spy_90,
       round(sum(market_value * uup_30) / nullif(sum(abs(market_value)) FILTER (WHERE uup_30 IS NOT NULL), 0), 3) AS uup_30,
       round(sum(market_value * uup_90) / nullif(sum(abs(market_value)) FILTER (WHERE uup_90 IS NOT NULL), 0), 3) AS uup_90,
       round(sum(market_value * tlt_30) / nullif(sum(abs(market_value)) FILTER (WHERE tlt_30 IS NOT NULL), 0), 3) AS tlt_30,
       round(sum(market_value * tlt_90) / nullif(sum(abs(market_value)) FILTER (WHERE tlt_90 IS NOT NULL), 0), 3) AS tlt_90,
       round(sum(market_value * gld_30) / nullif(sum(abs(market_value)) FILTER (WHERE gld_30 IS NOT NULL), 0), 3) AS gld_30,
       round(sum(market_value * gld_90) / nullif(sum(abs(market_value)) FILTER (WHERE gld_90 IS NOT NULL), 0), 3) AS gld_90,
       round(sum(market_value * uso_30) / nullif(sum(abs(market_value)) FILTER (WHERE uso_30 IS NOT NULL), 0), 3) AS uso_30,
       round(sum(market_value * uso_90) / nullif(sum(abs(market_value)) FILTER (WHERE uso_90 IS NOT NULL), 0), 3) AS uso_90
FROM mfr_book_corr GROUP BY as_of;
COMMIT;
