-- 087_eod_close_store.sql — UNADJUSTED daily closes for range-position
-- arithmetic (operator decision, 2026-09-06: live #MFR_<T>_RP must not
-- require a fresh TradingView CSV).
--
-- Why not the existing stores:
--   * eod_bar_store: pack-replay archive — no ^VIX/AAAU, ON CONFLICT DO
--     NOTHING freezes an unsettled newest close (UUP/USO 2026-09-04 off by
--     one cent, 0.028-0.036%), and its replay contract is all-or-nothing on
--     the pack's own symbol set, so foreign writers could poison rebuilds.
--   * shadow_snapshots.shadow_price: the shadow job runs post-open with no
--     forming-bar trim — an intraday print, not an EOD close.
--   * mfr_snapshots.price: whatever price the last fetch saw (intraday on
--     bumped rows).
--
-- Maintained by trendspider_export.py (daily 17:45 run, post-close):
-- auto_adjust=False bars, last ~15 sessions upserted every run so a
-- late-settling close is HEALED by the next run (unadjusted closes never
-- change again after settle — there is no dividend-adjustment hazard in
-- refreshing history).
--
-- Consumers: rp_live_from_stored_close (live #MFR_<T>_RP). TradingView
-- closes (tv_mfr_history.close) remain the HISTORICAL source only.
--
-- Apply via:  py apply_migration.py migrations/087_eod_close_store.sql

CREATE TABLE IF NOT EXISTS eod_close_store (
    ticker      text        NOT NULL,   -- bot/mfr ticker (^VIX, not VIX)
    bar_date    date        NOT NULL,
    close       numeric     NOT NULL CHECK (close > 0),
    source      text        NOT NULL DEFAULT 'yfinance-unadjusted',
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, bar_date)
);

COMMENT ON TABLE eod_close_store IS
    'Unadjusted official daily closes (auto_adjust=False), maintained by the '
    'TrendSpider export job for range-position arithmetic. Rolling upsert '
    'heals late-settling closes; values are never dividend-adjusted.';
