-- 014_eod_bar_store.sql — persist the RAW BARS behind every pack build.
--
-- WHY (2026-08-16). The pack was non-deterministic: two builds 22 minutes apart
-- on a closed market produced materially different 1M/3M/6M returns and 30D+
-- correlations. Root cause measured, not assumed:
--   * _fetch_bars asked yfinance for period='630d', which is not a documented
--     yfinance period at all.
--   * Row counts vary BETWEEN CALLS IN THE SAME PROCESS: SPHB 627 then 630,
--     XLRE 630 then 628, USMV 627 then 630.
--   * Every window was indexed by ROW OFFSET (cs[-1 - 21]), so a shifted row
--     count moved the comparison date: SPHB's "1M ago" was 2026-07-13 on one
--     call and 2026-07-16 on the next, giving 1M returns of +4.05% and +6.07%.
--   * Switching to an explicit start/end date range does NOT fix it -- three
--     identical ranged calls returned 617/617/614 rows for CPER. The upstream
--     feed is simply not reproducible.
--
-- Date-indexed windows make the OUTPUT robust to a missing row. They cannot
-- make a REBUILD byte-identical, because the input itself changes. That needs
-- the bars stored and replayed, which is what this table is for: the first
-- build for an as-of date writes the bars; every later build for that same
-- as-of reads them back and cannot drift.
--
-- One row per (as_of, symbol). `bars` is [[iso_date, close], ...] oldest-first.
CREATE TABLE IF NOT EXISTS eod_bar_store (
    as_of       DATE        NOT NULL,
    symbol      TEXT        NOT NULL,
    bars        JSONB       NOT NULL,
    n_bars      INTEGER     NOT NULL,
    first_bar   DATE,
    last_bar    DATE,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of, symbol)
);

CREATE INDEX IF NOT EXISTS eod_bar_store_asof_idx ON eod_bar_store (as_of);
