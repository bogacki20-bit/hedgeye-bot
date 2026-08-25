-- 082_pack_provenance.sql — the stamp must describe the machine that built
-- the pack.
--
-- 2026-08-25: _deployed_sha() preferred bot_state.bot_git_sha (stamped by the
-- RAILWAY bot) over local HEAD, so packs built on the Windows box recorded a
-- different machine's commit — and 079's dirty_tree (measured locally) sat
-- next to it, an actively wrong claim. These columns pin sha and machine to
-- the SAME process; the resolver in tools/eod_stat_pack.py is the one truth.
--
-- (080 and 081 are reserved by the queued position-caps job: book_cash and
-- position_cap_shadow. This file deliberately skips to 082.)

ALTER TABLE eod_pack_artifacts
  ADD COLUMN IF NOT EXISTS built_by text;      -- hostname, or 'railway'
ALTER TABLE eod_pack_artifacts
  ADD COLUMN IF NOT EXISTS sha_source text;    -- 'local-git' | 'railway-env'
                                               -- | 'bot_state' | 'unknown'
                                               -- | 'unknown-legacy' (backfill)
