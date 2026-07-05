-- 051_etfpro_week_of_relabel.sql — correct ETF Pro weekly week_of labels.
--
-- BUG (fixed in code by parser_etf_pro._monday_of, commit 73b5cf3): the ETF Pro
-- weekly ranges report is published on SUNDAY for the week ahead, but the parser
-- dated it with `received - weekday()`, which maps a Sunday to the PRIOR Monday. So
-- every Sunday-published weekly was labeled a week behind and UPSERTED OVER the prior
-- week's rows (PK = ticker, week_of) instead of advancing — freezing max(week_of) a
-- week stale even though the data was current.
--
-- This migration relabels the already-ingested history to the correct week (a uniform
-- +7-day shift for the weekend-published weeklies; weekday rows unchanged). Applied
-- once to production on 2026-07-05: 17 emails / 659 rows moved, 772 total rows
-- preserved, max(week_of) 06-22 -> 06-29, zero (ticker, week_of) duplicates.
--
-- IDEMPOTENT + COLLISION-SAFE: the shift is a cascade (each week's correct slot is
-- held by the next week's data, which also shifts), so a naive bulk UPDATE would hit
-- the PK. Done in two phases — park every mislabeled row far in the future (vacating
-- all old slots), then bring them back to the correct week. Guarded by
-- correct_week <> week_of, so it is a no-op against already-corrected data (and against
-- a fresh parse, since the fixed _monday_of already labels new rows correctly).

BEGIN;

-- Phase A — park every mislabeled row at (correct_week + 1000y). Vacates all old slots.
WITH corrected AS (
    SELECT r.ctid,
           r.week_of AS cur_week,
           CASE WHEN EXTRACT(DOW FROM e.received_at) IN (0, 6)   -- Sun(0) / Sat(6)
                THEN date_trunc('week', e.received_at)::date + 7  -- -> upcoming Monday
                ELSE date_trunc('week', e.received_at)::date      -- -> this week's Monday
           END AS correct_week
    FROM hedgeye_etf_pro_ranges r
    JOIN hedgeye_emails_raw e ON e.message_id = r.source_email_id
    WHERE e.received_at IS NOT NULL
)
UPDATE hedgeye_etf_pro_ranges t
   SET week_of = (c.correct_week + INTERVAL '1000 years')::date
  FROM corrected c
 WHERE t.ctid = c.ctid
   AND c.correct_week <> c.cur_week;

-- Phase B — bring the parked rows back to their correct week (old slots now empty).
UPDATE hedgeye_etf_pro_ranges
   SET week_of = (week_of - INTERVAL '1000 years')::date
 WHERE week_of > DATE '3000-01-01';

COMMIT;
