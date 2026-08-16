-- 013_eod_pack_artifacts.sql — persist every generated EOD stat pack.
--
-- WHY (2026-08-16). The pack printed "last bar 2026-08-16" on a SUNDAY. When
-- it came time to diagnose, the artifact did not exist anywhere: not on disk,
-- not in the database. The bug was therefore UNFALSIFIABLE — the deployed code
-- was byte-identical to what reproduced correctly locally, and there was no
-- record of what the failing run actually saw. That is a defect in its own
-- right, independent of the bar-date bug.
--
-- Every column here exists to answer a question that could not be answered
-- during that investigation:
--   built_at          when did this run happen (the header said 10:27 ET)
--   last_bar_date     what date did it RESOLVE, before any validation
--   bar_date_valid    did the calendar assertion pass
--   block_reason      if it blocked, why
--   deployed_sha      WHICH BUILD produced this — the first thing asked for,
--                     and the only way to distinguish a code defect from an
--                     environment defect
--   symbols_ok/total  fetch completeness at the time
--   body              the full rendered text, verbatim
CREATE TABLE IF NOT EXISTS eod_pack_artifacts (
    id              BIGSERIAL PRIMARY KEY,
    built_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    built_at_et     TEXT,
    last_bar_date   DATE,
    bar_date_valid  BOOLEAN,
    block_reason    TEXT,
    deployed_sha    TEXT,
    symbols_ok      INTEGER,
    symbols_total   INTEGER,
    body            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS eod_pack_artifacts_built_idx
    ON eod_pack_artifacts (built_at DESC);
CREATE INDEX IF NOT EXISTS eod_pack_artifacts_bar_idx
    ON eod_pack_artifacts (last_bar_date);
