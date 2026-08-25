-- 079_pack_dirty_tree.sql — record whether the working tree was DIRTY when
-- an EOD pack was built.
--
-- 2026-08-25 audit: _persist_pack stamps a sha, but a pack built from a
-- dirty tree stamps a clean-looking sha and is indistinguishable from one
-- built from that commit exactly. Eight packs built from unmerged branch
-- code on 8/24-8/25 all classified ON-MASTER for exactly this reason.
--
-- NULL means "we could not have known" — the honest value for every row
-- written before this column existed. No backfill.

ALTER TABLE eod_pack_artifacts
  ADD COLUMN IF NOT EXISTS dirty_tree boolean;
ALTER TABLE eod_pack_artifacts
  ADD COLUMN IF NOT EXISTS dirty_tracked_n integer;
