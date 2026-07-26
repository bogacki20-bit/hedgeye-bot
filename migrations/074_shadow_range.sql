-- 074_shadow_range.sql — parallel shadow range computation (shadow_range.py)
--
-- Stores MFR-style ranges computed from daily OHLC alone, ALONGSIDE the MFR feed.
-- Deliberately a SEPARATE table, not new columns on mfr_snapshots:
--   v_screener builds latest_mfr as SELECT DISTINCT ON (ticker) ... ORDER BY
--   ticker, snapshot_date DESC. A shadow row inserted at (ticker, today) with
--   NULL range_low/range_high would become the "latest" row and blank the real
--   MFR range for every ticker whose newest MFR row is older than today (the
--   crypto orphans at 2026-06-30, EUR/GBP/BTC at 2026-07-25, the 11 dark names).
--   A separate table cannot affect latest_mfr, so nothing downstream changes.
--
-- Join to MFR with:  ... FROM mfr_snapshots m JOIN shadow_snapshots s
--                    USING (ticker, snapshot_date)
--
-- Nothing reads this table yet. Read-side wiring is a later step.

CREATE TABLE IF NOT EXISTS shadow_snapshots (
    ticker           text        NOT NULL,
    snapshot_date    date        NOT NULL,

    -- RangeSnapshot fields (NULL when status <> 'ok')
    shadow_price     numeric,
    shadow_low       numeric,
    shadow_high      numeric,
    shadow_rp        numeric,
    shadow_hurst     numeric,
    shadow_sigma     numeric,     -- blended daily vol, decimal
    shadow_trend     text,        -- BULL / BEAR / NEUT
    shadow_momentum  numeric,     -- fast-EMA slope, % per day

    -- provenance / auditability
    bars             integer,     -- daily bars fed to compute_range
    status           text        NOT NULL DEFAULT 'ok',
                                  -- ok | insufficient_bars | no_data | error
    note             text,        -- error detail when status <> 'ok'
    params_hash      text,        -- RangeParams fingerprint (defaults are UNCALIBRATED)
    computed_at      timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (ticker, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_shadow_snapshots_date
    ON shadow_snapshots (snapshot_date DESC);

CREATE INDEX IF NOT EXISTS idx_shadow_snapshots_status
    ON shadow_snapshots (status) WHERE status <> 'ok';

COMMENT ON TABLE shadow_snapshots IS
    'Shadow (OHLC-derived) MFR-style ranges from shadow_range.compute_range(). '
    'Parallel to mfr_snapshots, never a replacement. Params are UNCALIBRATED '
    'priors - see SHADOW_RANGE_INTEGRATION.md step 2 before trusting these numbers.';
