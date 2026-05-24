-- 029_ibkr_conid_cache.sql
-- Per-ticker conid cache for IBKR market-data lookups. IBKR's API addresses
-- contracts by integer conid (e.g. SPY -> 756733), not by ticker symbol, so
-- every snapshot/quote call needs a conid. Caching avoids re-hitting
-- /v1/api/iserver/secdef/search on every cycle.
--
-- ticker        — uppercased ticker as the bot uses it (matches mfr_snapshots.ticker)
-- conid         — IBKR's internal contract identifier (bigint; some are 9-10 digits)
-- exchange      — listing exchange, populated when the search returns it
--                 (helpful for ambiguity, e.g. dual-listed names)
-- sec_type      — STK / ETF / IND / FUT / etc. — passed back from secdef/search
-- last_verified — bumped each successful snapshot fetch; lets us re-resolve
--                 stale conids if a ticker gets re-listed under a new id
--
-- Caller pattern (tools/price_feed_ibkr.resolve_conid):
--   1. SELECT conid FROM ibkr_conid_cache WHERE ticker = ?
--   2. miss → GET /v1/api/iserver/secdef/search → INSERT
--   3. hit  → use cached conid; on a successful quote fetch, UPDATE last_verified

CREATE TABLE IF NOT EXISTS ibkr_conid_cache (
    ticker        TEXT PRIMARY KEY,
    conid         BIGINT NOT NULL,
    exchange      TEXT,
    sec_type      TEXT,
    last_verified TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ibkr_conid ON ibkr_conid_cache(conid);
