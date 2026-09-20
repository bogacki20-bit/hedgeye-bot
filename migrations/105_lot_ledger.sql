-- 105_lot_ledger.sql — the FILL-LEVEL LOT LEDGER (desk spec, 9/20).
--
-- Positions were tracked as one blended average; Kris scales in and out,
-- so the average destroys what the doctrine runs on (JETS read "+1.0%
-- over 53 sessions" and tripped the stale clock while being scaled INTO).
-- One row per fill, with the CONTEXT AT FILL that makes the framework
-- testable: price (the broker-reconcilable fact), the range live at that
-- moment (lo/hi + hdg|mfr tag), rp computed from both, trend, SPX tilt,
-- dealer walls. Store all three of price/range/rp: price alone makes rp
-- unrecoverable; rp alone is unreconcilable against Fidelity.
--
-- Everything else DERIVES: FIFO lots, oldest-open-lot (the clock), per-
-- lot P/L, times rented, the shelf. Options are excluded (spreads are
-- modeled as structures, not lots — build list #7).

BEGIN;

CREATE TABLE IF NOT EXISTS fills (
    id              BIGSERIAL PRIMARY KEY,
    actions_log_id  BIGINT UNIQUE,       -- provenance; NULL for manual rows
    ticker          TEXT        NOT NULL,
    account_number  TEXT        NOT NULL,
    run_date        DATE        NOT NULL,
    side            TEXT        NOT NULL,  -- long | short
    action          TEXT        NOT NULL,  -- open|add|trim|cover_some|cover_all|close
    qty             NUMERIC     NOT NULL,  -- absolute shares
    price           NUMERIC,
    amount          NUMERIC,
    position_after  NUMERIC,               -- signed shares after this fill
    -- context at fill (point-in-time; never overwritten later)
    rp_at_fill      NUMERIC,
    range_lo        NUMERIC,
    range_hi        NUMERIC,
    range_src       TEXT,                  -- hdg | mfr
    trend_at_fill   TEXT,
    spx_tilt        NUMERIC,
    call_wall       NUMERIC,
    hedge_wall      NUMERIC,
    put_wall        NUMERIC,
    fill_source     TEXT,                  -- research | screen | discretionary | NULL
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_fills_tkr  ON fills (ticker, account_number, run_date);
CREATE INDEX IF NOT EXISTS ix_fills_date ON fills (run_date);

COMMIT;
