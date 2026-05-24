-- 028_mfr_typed_walls.sql
-- Type the MFR gamma-walls + IV from full_payload->'gammaMetrics' into
-- queryable columns. The data is already in mfr_snapshots.full_payload
-- JSONB on every row where MFR provided gammaMetrics (~55% of recent rows —
-- US equities with listed options); this migration just exposes it as
-- typed columns so decision_engine.decide_notifier can read it cheaply.
--
-- Source paths in payload:
--   call_wall_mfr   ← gammaMetrics.gamma.callWallLevel
--   put_wall_mfr    ← gammaMetrics.gamma.putWallLevel
--   zero_gamma      ← gammaMetrics.gamma.zeroGamma            (gamma-flip)
--   absolute_gamma  ← gammaMetrics.gamma.absoluteGammaLevel   (peak strike)
--   iv30_mfr        ← gammaMetrics.gamma.quote.iv30
--
-- Note: spec called for filename 013_mfr_typed_walls.sql but 013 is taken
-- (013_hedgeye_rta.sql). Using 028 to keep the sequence linear.

ALTER TABLE mfr_snapshots
  ADD COLUMN IF NOT EXISTS call_wall_mfr   NUMERIC,
  ADD COLUMN IF NOT EXISTS put_wall_mfr    NUMERIC,
  ADD COLUMN IF NOT EXISTS zero_gamma      NUMERIC,
  ADD COLUMN IF NOT EXISTS absolute_gamma  NUMERIC,
  ADD COLUMN IF NOT EXISTS iv30_mfr        NUMERIC;

-- Backfill from existing JSONB. WHERE clause guards rows where MFR returned
-- gammaMetrics=null (commodities futures, FX, VIX, thin equities — ~45% of
-- recent rows). NULLIF on the text->numeric cast handles "null" string
-- artifacts if any creep through.
UPDATE mfr_snapshots
SET call_wall_mfr  = NULLIF(full_payload->'gammaMetrics'->'gamma'->>'callWallLevel', 'null')::numeric,
    put_wall_mfr   = NULLIF(full_payload->'gammaMetrics'->'gamma'->>'putWallLevel',  'null')::numeric,
    zero_gamma     = NULLIF(full_payload->'gammaMetrics'->'gamma'->>'zeroGamma',     'null')::numeric,
    absolute_gamma = NULLIF(full_payload->'gammaMetrics'->'gamma'->>'absoluteGammaLevel', 'null')::numeric,
    iv30_mfr       = NULLIF(full_payload->'gammaMetrics'->'gamma'->'quote'->>'iv30', 'null')::numeric
WHERE full_payload->'gammaMetrics'->'gamma' IS NOT NULL
  AND full_payload->'gammaMetrics'->'gamma' != 'null'::jsonb;
