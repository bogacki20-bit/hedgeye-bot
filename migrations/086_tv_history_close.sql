-- 086_tv_history_close.sql — store the TV bar close in tv_mfr_history.
--
-- Needed for the live-side RP definition (operator decision, 2026-09-06):
-- rp for bar D = (close[D-1] - range_low[D]) / (range_high[D] - range_low[D]),
-- clamped [-0.5, 1.5], where close[D-1] is the prior session close from the
-- SAME source the historical rows used — the TradingView bars. The exporter
-- joins each live mfr_snapshots row to the latest earlier TV close; rows with
-- no sufficiently fresh TV close are skipped loudly, never approximated.
--
-- Populated by re-running:  py tradingview_ingest.py --all
--
-- Apply via:  py apply_migration.py migrations/086_tv_history_close.sql

ALTER TABLE tv_mfr_history ADD COLUMN IF NOT EXISTS close numeric;

COMMENT ON COLUMN tv_mfr_history.close IS
    'TV daily bar close - the close source for range-position arithmetic '
    '(historical AND live-side RP). NULL only on rows ingested before 086.';
