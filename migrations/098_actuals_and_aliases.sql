-- 098: (a) seed hedgeye_quad_actual with Hedgeye's printed actuals
-- (operator-supplied 2026-09-08; source HE_3Q26_Macro_Themes.pdf slide 25,
-- 6/24/2026; 1Q26 GDP restated 2.68 on 8/13 (quad unchanged); 2Q26 from
-- the 8/13 update). Their 8/13 forward estimates, for the record, NOT
-- rows here: 3Q26E 1.98/3.43/Quad4 - 4Q26E 2.23/3.61/Quad2 -
-- 1Q27E 2.12/3.30/Quad4 - 2Q27E 2.27/1.72/Quad1.
-- (b) normalize the 2026-02-13..02-26 Risk Range alias window (the mails
-- briefly used futures/index-style symbols duplicating the canonical
-- slash-style names).
INSERT INTO hedgeye_quad_actual (quarter, gdp_yoy, cpi_yoy, quad, source, source_note_date) VALUES
    ('4Q22', 1.32, 7.10, 4, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('1Q23', 2.31, 5.81, 1, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('2Q23', 2.79, 3.98, 1, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('3Q23', 3.23, 3.51, 1, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('4Q23', 3.39, 3.24, 1, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('1Q24', 2.86, 3.24, 4, 'HE_3Q26_Macro_Themes.pdf slide 25 (CPI flat; HE=decel)', '2026-06-24'),
    ('2Q24', 3.13, 3.20, 1, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('3Q24', 2.79, 2.62, 4, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('4Q24', 2.40, 2.75, 3, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('1Q25', 2.02, 2.74, 4, 'HE_3Q26_Macro_Themes.pdf slide 25 (CPI -1bp; HE=decel)', '2026-06-24'),
    ('2Q25', 2.08, 2.45, 1, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('3Q25', 2.34, 2.88, 2, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('4Q25', 1.99, 2.71, 4, 'HE_3Q26_Macro_Themes.pdf slide 25', '2026-06-24'),
    ('1Q26', 2.68, 2.69, 1, 'HE_3Q26_Macro_Themes.pdf slide 25; GDP restated 8/13 from 2.57', '2026-08-13'),
    ('2Q26', 2.10, 3.86, 3, 'hedgeye 8/13 mid-quarter restatement (deck), operator-confirmed', '2026-08-13')
ON CONFLICT (quarter) DO UPDATE SET
    gdp_yoy = EXCLUDED.gdp_yoy, cpi_yoy = EXCLUDED.cpi_yoy,
    quad = EXCLUDED.quad, source = EXCLUDED.source,
    source_note_date = EXCLUDED.source_note_date;

-- (b) Feb-2026 alias normalization. Delete alias rows that would collide
-- with an existing canonical row for the same day (canonical wins), then
-- rename the rest.
CREATE TEMP TABLE _alias_map (alias text PRIMARY KEY, canon text);
INSERT INTO _alias_map VALUES
    ('EURUSD','EUR/USD'), ('GBPUSD','GBP/USD'), ('CADUSD','CAD/USD'),
    ('USDJPY','USD/YEN'), ('GCUSD','GOLD'), ('SILUSD','SILVER'),
    ('CLUSD','WTIC'), ('BZUSD','BRENT'), ('NGUSD','NATGAS'),
    ('HGUSD','COPPER'), ('GDAXI','DAX'), ('N225','NIKK'),
    ('IXIC','COMPQ'), ('SS','SSEC'), ('Y.NYB','USD'), ('BTCUSD','BITCOIN');
DELETE FROM hedgeye_risk_ranges r
USING _alias_map m
WHERE r.ticker = m.alias
  AND EXISTS (SELECT 1 FROM hedgeye_risk_ranges c
              WHERE c.ticker = m.canon AND c.signal_date = r.signal_date);
UPDATE hedgeye_risk_ranges r
SET ticker = m.canon
FROM _alias_map m
WHERE r.ticker = m.alias;
DROP TABLE _alias_map;
