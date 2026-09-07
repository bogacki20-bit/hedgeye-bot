-- 096: nowcast_vs_realized — Hedgeye's real-time stated Quad vs the
-- realized Quad (quad_monthly, FRED-derived). "Stated" = the LATEST note
-- on or before the period's end that mentions the period (majority of
-- mentions within that note breaks prose conflicts). Last-in-period, not
-- a whole-period vote: the 2Q26 case is the June Quad-4 nowcast vs
-- realized Quad 3 — a whole-period vote would let months of earlier
-- "2Q26 = Quad 3" mentions bury the final call and fake a match.
CREATE OR REPLACE VIEW nowcast_vs_realized AS
WITH qwin AS (
    SELECT DISTINCT ON (period) period,
           make_date(2000 + right(period, 2)::int,
                     left(period, 1)::int * 3 - 2, 1) AS ref_month,
           quad, note_date AS last_note
    FROM hedgeye_quad_stated
    WHERE scope = 'quarterly' AND period ~ '^[1-4]Q[0-9]{2}$'
      AND note_date < make_date(2000 + right(period, 2)::int,
                                left(period, 1)::int * 3 - 2, 1)
                      + interval '3 months'
    ORDER BY period, note_date DESC, n_hits DESC, quad
), mwin AS (
    SELECT DISTINCT ON (period) period,
           to_date(period || '-01', 'YYYY-MM-DD') AS ref_month,
           quad, note_date AS last_note
    FROM hedgeye_quad_stated
    WHERE scope = 'monthly' AND period ~ '^[0-9]{4}-[0-9]{2}$'
      AND to_char(note_date, 'YYYY-MM') <= period
    ORDER BY period, note_date DESC, n_hits DESC, quad
)
SELECT 'quarterly' AS scope, w.period, w.ref_month, w.quad AS stated_quad,
       w.last_note, r.quad AS realized_quad, r.known_at AS realized_known_at,
       (w.quad = r.quad) AS match
FROM qwin w LEFT JOIN quad_monthly r ON r.month = w.ref_month
UNION ALL
SELECT 'monthly', w.period, w.ref_month, w.quad, w.last_note,
       r.quad, r.known_at, (w.quad = r.quad)
FROM mwin w LEFT JOIN quad_monthly r ON r.month = w.ref_month
ORDER BY ref_month, scope;
