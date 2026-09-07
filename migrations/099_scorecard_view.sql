-- 099: final audited scorecard view (operator sign-off 2026-09-08).
-- nowcast_vs_realized, quarterly scope, rebuilt with:
--   * method tiers: explicit period-tagged statements (gip/qtag/mtag)
--     outrank bare prose within a note, then n_hits, then lowest quad
--   * three horizons: H1 first call >= 90d before quarter start,
--     H2 last call before quarter start, H3 last call in-quarter
--   * comparators: FRED-realized quad_monthly (2bp CPI tie rule,
--     reconciled 15/15 vs Hedgeye's printed actuals) AND
--     hedgeye_quad_actual; def_gap flags residual disagreement
--   * scoring: 1 = match, 0.5 = the winning note also stated the
--     realized quad for the period (hedged/dual statement, e.g. 4Q24
--     "Quad2 or Quad3? Yes"), 0 = miss
-- The monthly dial lives in quad_nowcast_daily (same method rules).
DROP VIEW IF EXISTS nowcast_vs_realized;
CREATE VIEW nowcast_vs_realized AS
WITH s AS (
    SELECT period, note_date, quad, n_hits,
           CASE WHEN method IN ('gip', 'qtag', 'mtag') THEN 1 ELSE 2 END AS mrank,
           make_date(2000 + right(period, 2)::int,
                     left(period, 1)::int * 3 - 2, 1) AS qstart
    FROM hedgeye_quad_stated
    WHERE scope = 'quarterly' AND period ~ '^[1-4]Q[0-9]{2}$'
), pick AS (   -- chosen note per period x horizon
    SELECT period, h,
           CASE WHEN h = 1 THEN min(note_date) ELSE max(note_date) END AS nd
    FROM s CROSS JOIN (VALUES (1), (2), (3)) AS hz(h)
    WHERE note_date <= CASE h
        WHEN 1 THEN qstart - 90
        WHEN 2 THEN qstart - 1
        ELSE (qstart + interval '3 months' - interval '1 day')::date END
    GROUP BY period, h
), votes AS (  -- method-tier + n_hits vote within the chosen note
    SELECT period, h, nd, quad, sum(n_hits) AS hits
    FROM (
        SELECT p.period, p.h, p.nd, s.quad, s.n_hits, s.mrank,
               min(s.mrank) OVER (PARTITION BY p.period, p.h) AS mr
        FROM pick p JOIN s ON s.period = p.period AND s.note_date = p.nd
    ) z
    WHERE mrank = mr
    GROUP BY period, h, nd, quad
), win AS (
    SELECT DISTINCT ON (period, h) period, h, nd, quad
    FROM votes ORDER BY period, h, hits DESC, quad
), flat AS (
    SELECT w.period, s0.qstart,
           max(w.quad) FILTER (WHERE h = 1) AS h1_quad,
           max(w.nd)   FILTER (WHERE h = 1) AS h1_note,
           max(w.quad) FILTER (WHERE h = 2) AS h2_quad,
           max(w.nd)   FILTER (WHERE h = 2) AS h2_note,
           max(w.quad) FILTER (WHERE h = 3) AS h3_quad,
           max(w.nd)   FILTER (WHERE h = 3) AS h3_note
    FROM win w
    JOIN (SELECT DISTINCT period, qstart FROM s) s0 USING (period)
    GROUP BY w.period, s0.qstart
)
SELECT f.period, f.qstart AS ref_month,
       f.h1_quad, f.h1_note, f.h2_quad, f.h2_note, f.h3_quad, f.h3_note,
       q.quad AS fred_quad, a.quad AS he_actual_quad,
       (q.quad IS NOT NULL AND a.quad IS NOT NULL
        AND q.quad <> a.quad) AS def_gap,
       CASE WHEN a.quad IS NULL OR f.h3_quad IS NULL THEN NULL
            WHEN f.h3_quad = a.quad THEN 1.0
            WHEN EXISTS (SELECT 1 FROM hedgeye_quad_stated d
                         WHERE d.scope = 'quarterly' AND d.period = f.period
                           AND d.note_date = f.h3_note AND d.quad = a.quad)
                 THEN 0.5 ELSE 0.0 END AS h3_score_vs_he,
       CASE WHEN a.quad IS NULL OR f.h2_quad IS NULL THEN NULL
            WHEN f.h2_quad = a.quad THEN 1.0
            WHEN EXISTS (SELECT 1 FROM hedgeye_quad_stated d
                         WHERE d.scope = 'quarterly' AND d.period = f.period
                           AND d.note_date = f.h2_note AND d.quad = a.quad)
                 THEN 0.5 ELSE 0.0 END AS h2_score_vs_he
FROM flat f
LEFT JOIN quad_monthly q ON q.month = f.qstart
LEFT JOIN hedgeye_quad_actual a ON a.quarter = f.period
ORDER BY f.qstart;
