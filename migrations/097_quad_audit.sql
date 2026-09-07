-- 097: scorecard audit support (roadmap §2, A2/A3).
-- A2: hedgeye_quad_actual — Hedgeye's OWN printed actuals for closed
-- quarters. Email text cannot populate this systematically: the GIP
-- tables (including "(actual)" rows) ship as deck images, verified
-- three ways against the full 6,814-body corpus (0 "(actual)" pattern
-- hits; 0/135 quarterly statements carry numbers; the X% (NQNNE)
-- numeric style appears in exactly 1 mail). Seeded with the
-- operator-confirmed 8/13/2026 restatement; the rest awaits the
-- MASTER_THE_MARKET historical quad table or deck-table transcription.
CREATE TABLE IF NOT EXISTS hedgeye_quad_actual (
    quarter          text PRIMARY KEY CHECK (quarter ~ '^[1-4]Q[0-9]{2}$'),
    gdp_yoy          numeric,
    cpi_yoy          numeric,
    quad             int NOT NULL CHECK (quad BETWEEN 1 AND 4),
    source           text NOT NULL,
    source_note_date date
);
INSERT INTO hedgeye_quad_actual VALUES
    ('2Q26', 2.10, 3.86, 3, 'hedgeye 8/13 mid-quarter restatement (deck), operator-confirmed', '2026-08-13')
ON CONFLICT (quarter) DO NOTHING;

-- A3: statement method — explicit period-tagged beats bare prose.
--   gip   = full GIP row (period + both %s + quad)
--   qtag  = explicit quarter-tagged ("3Q25 = Quad 3")
--   mtag  = explicit month-tagged ("August Quad 3" path strings)
--   prose = bare Quad mention attributed by context
ALTER TABLE hedgeye_quad_stated ADD COLUMN IF NOT EXISTS method text;
