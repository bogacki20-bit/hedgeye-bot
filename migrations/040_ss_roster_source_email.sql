-- 040_ss_roster_source_email.sql — add source_email_id to ss_roster_history.
-- apply_deltas (step 3) records which SS email drove each delta-add, but the 039
-- schema omitted the column (caught by the step-4 real-write test before any live
-- add). Additive + idempotent. Reversible: ALTER TABLE ... DROP COLUMN source_email_id.
ALTER TABLE ss_roster_history ADD COLUMN IF NOT EXISTS source_email_id TEXT;
