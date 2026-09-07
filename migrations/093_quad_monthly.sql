-- 093_quad_monthly.sql — Phase B(b): realized Quad from FRED
-- (docs/ROUND2_DATA_ROADMAP.md section 1; operator brief 2026-09-07).
--
-- Rule: Quad = sign of D(YoY real GDP growth) x sign of D(YoY CPI) vs the
-- prior QUARTER. Q1 G+I- / Q2 G+I+ / Q3 G-I+ / Q4 G-I-. GDPC1 is
-- quarterly, so every month of quarter Q carries Q's quad, and known_at is
-- bound by the GDP ADVANCE release: approximated conservatively as the
-- FIRST DAY OF THE SECOND MONTH after quarter end + 0 days (i.e. later
-- than the real late-January-style advance print — the safe direction; we
-- have no ALFRED vintage feed). A bar on date D joins the latest row with
-- known_at <= D.
--
-- Source CSVs are banked in data/reference/fred/ (corpus-first).
-- Cross-check against Hedgeye's own MASTER_THE_MARKET table is NOTED AS
-- PENDING - that document is not in this repo; where they disagree,
-- Hedgeye's print wins per the roadmap.
--
-- Apply via:  py apply_migration.py migrations/093_quad_monthly.sql

CREATE TABLE IF NOT EXISTS quad_monthly (
    month     date        NOT NULL,   -- first day of the month described
    quad      integer     NOT NULL CHECK (quad BETWEEN 1 AND 4),
    g_roc     numeric     NOT NULL,   -- D(YoY GDP growth) vs prior quarter, pct pts
    i_roc     numeric     NOT NULL,   -- D(YoY CPI) vs prior quarter, pct pts
    known_at  timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (month)
);
