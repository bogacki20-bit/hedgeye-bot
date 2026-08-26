-- 083_mfr_published_position.sql — store the range position MFR PUBLISHES
-- instead of only re-deriving it.
--
-- 2026-08-26 (PART B evidence): the MFR payload carries
-- rangeData.positionOnRange and ltRangeData.positionOnRange — the exact
-- short-term and long-term percentages the dashboard shows — and the bot
-- never stored either. Every band error silently became a wrong rp with
-- nothing to check it against (H3). HYG read 1.29 while MFR published 0.89:
-- the displayed value came from a fresh-but-narrower Hedgeye trade band via
-- v_screener's override, and no cross-check existed to catch it.
--
-- Backfill is from full_payload (already stored per row) — no network, no
-- rewriting of any existing range/price value. (080/081 remain reserved by
-- the queued position-caps job.)

ALTER TABLE mfr_snapshots
  ADD COLUMN IF NOT EXISTS mfr_pos_short numeric;   -- published ST position, 0-1 scale (can exceed [0,1])
ALTER TABLE mfr_snapshots
  ADD COLUMN IF NOT EXISTS mfr_pos_long numeric;    -- published LT position
ALTER TABLE mfr_snapshots
  ADD COLUMN IF NOT EXISTS rp_source text;          -- 'mfr-published' | 'derived-mfr' | 'shadow' | 'wrapper'

UPDATE mfr_snapshots
   SET mfr_pos_short = (full_payload->'rangeData'->>'positionOnRange')::numeric
 WHERE mfr_pos_short IS NULL
   AND full_payload->'rangeData'->>'positionOnRange' IS NOT NULL;

UPDATE mfr_snapshots
   SET mfr_pos_long = (full_payload->'ltRangeData'->>'positionOnRange')::numeric
 WHERE mfr_pos_long IS NULL
   AND full_payload->'ltRangeData'->>'positionOnRange' IS NOT NULL;

UPDATE mfr_snapshots
   SET rp_source = CASE WHEN mfr_pos_short IS NOT NULL THEN 'mfr-published'
                        ELSE 'derived-mfr' END
 WHERE rp_source IS NULL;

-- D4: the divergence ledger — published vs derived disagreeing by > 0.05.
-- PK gives the once-per-ticker-per-day squawk dedup for free.
CREATE TABLE IF NOT EXISTS rp_divergence (
  seen_on    date    NOT NULL,
  ticker     text    NOT NULL,
  published  numeric NOT NULL,
  derived    numeric NOT NULL,
  delta      numeric NOT NULL,
  band_note  text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (seen_on, ticker)
);
