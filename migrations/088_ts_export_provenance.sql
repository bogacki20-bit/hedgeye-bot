-- 088_ts_export_provenance.sql — provenance flags on ts_export_log
-- (operator decision, 2026-09-06, TLT waiver).
--
-- TLT's live feed range diverges from the TV MFR indicator (53/81 overlap
-- dates, 0.07-0.29%), so TLT's TrendSpider range symbols are sourced
-- ENTIRELY from the TV indicator — history AND live period, no
-- mfr_snapshots union — keeping train and live on one definition. Those
-- rows carry source='tv_indicator', feed_verified=false. The trading desk
-- (REPORT/SCREEN) keeps the feed as TLT's source of truth; the waiver is
-- TrendSpider-only.
--
-- Apply via:  py apply_migration.py migrations/088_ts_export_provenance.sql

ALTER TABLE ts_export_log ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE ts_export_log ADD COLUMN IF NOT EXISTS feed_verified boolean;

COMMENT ON COLUMN ts_export_log.source IS
    'Row provenance where it matters: tv_indicator = TradingView MFR '
    'indicator export (no live-feed verification). NULL = default corpus '
    'provenance per the symbol''s extractor.';
COMMENT ON COLUMN ts_export_log.feed_verified IS
    'false = the value could NOT be verified against the live feed (TLT '
    'ranges, 2026-09-06 waiver). NULL = verification not applicable.';
