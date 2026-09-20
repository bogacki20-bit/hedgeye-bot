-- 108_energy_cracks.sql — daily energy-complex snapshot (operator 9/20:
-- diesel, CL1 and crack spreads belong in MARKET and the EOD report —
-- the crack spread is the earnings driver for the refiner sleeve
-- VLO/MPC/PSX/CVI/CRAK). One row per date; deltas derive.

BEGIN;

CREATE TABLE IF NOT EXISTS energy_cracks_daily (
    as_of        DATE PRIMARY KEY,
    cl1          NUMERIC,     -- WTI front month $/bbl (CL=F)
    ho1          NUMERIC,     -- ULSD/diesel front month $/gal (HO=F)
    rb1          NUMERIC,     -- RBOB gasoline front month $/gal (RB=F)
    diesel_crack NUMERIC,     -- HO*42 - CL   $/bbl
    gas_crack    NUMERIC,     -- RB*42 - CL   $/bbl
    crack_321    NUMERIC,     -- (2*RB*42 + HO*42 - 3*CL)/3  $/bbl
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
