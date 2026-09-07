-- 095: weight the stated-quad daily dial by raw mention frequency.
-- The PK (note_date, product, scope, period, quad) collapses repeated
-- prose mentions, so a note saying Quad 4 three times and Quad 1 once
-- would tie in a row-count vote. n_hits preserves the raw count.
ALTER TABLE hedgeye_quad_stated
    ADD COLUMN IF NOT EXISTS n_hits int NOT NULL DEFAULT 1;
