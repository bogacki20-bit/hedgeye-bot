-- 141: SpotGamma Tape, read every 15 minutes (Kris 2026-11-15: "the tape needs to be read every 15 min").
-- Captured from Kris's signed-in Chrome by tools/sg_tape_capture.js, loaded by tools/sg_tape.py.
-- sg_tape (migration-less, June-Oct text scrape) stays as history.
BEGIN;
CREATE TABLE IF NOT EXISTS sg_tape_reads (
    read_at     timestamptz PRIMARY KEY,
    stamp       text,
    vol_put     numeric, vol_call    numeric,
    prem_put    numeric, prem_call   numeric,
    delta_put   numeric, delta_call  numeric,
    gamma_put   numeric, gamma_call  numeric,
    vega_put    numeric, vega_call   numeric,
    n_prints    int,
    lists       jsonb,          -- largest trades (premium), top volume, top gamma notional, top movers
    loaded_at   timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sg_tape_prints (
    tns         text PRIMARY KEY,
    ts          timestamptz NOT NULL,
    sym         text NOT NULL,
    aggressor   text,           -- buy / sell
    side        text,           -- trade side: b(id) / m(id) / a(sk), bb / aa = below bid / above ask
    is_put      boolean,
    strike      numeric,
    expiry      timestamptz,
    size        int,
    premium     numeric,
    spot        numeric,
    price       numeric, bid numeric, ask numeric,
    iv          numeric,
    delta       numeric, gamma numeric, vega numeric,
    day_vol     numeric,
    prev_oi     numeric,
    is_block    boolean, is_sweep boolean, is_spread boolean, is_cross boolean,
    is_retail   boolean, next_exp boolean,
    cond        text,
    read_at     timestamptz
);
CREATE INDEX IF NOT EXISTS sg_tape_prints_ts_idx ON sg_tape_prints (ts);
CREATE INDEX IF NOT EXISTS sg_tape_prints_sym_idx ON sg_tape_prints (sym, ts);
COMMIT;
