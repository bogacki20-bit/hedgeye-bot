-- 122_ai_short_watch.sql — the AI-bubble short watch list (Kris, 2026-10-19).
-- Source: Hedgeye "The Big Theme: The AI Bubble" deck, 10/18 update, slide 53
-- (TREND by group: 26 of 36 bearish) + slide 45 (severity if it breaks).
-- Kris: stalk these for shorts on bounces, neoclouds first.
-- tools/ai_short_watch.py alerts when a BEARISH-trend name bounces into the
-- top of its range or up to its TRADE line. Re-seed from each Sunday deck.
BEGIN;

CREATE TABLE IF NOT EXISTS ai_short_watch (
    ticker        TEXT PRIMARY KEY,
    layer         TEXT NOT NULL,        -- deck stack layer / severity cohort
    severity_pct  NUMERIC,              -- deck's est. peak-to-trough if it breaks
    deck_trend    TEXT,                 -- deck slide-53 TREND at seed time
    deck_date     DATE NOT NULL,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    notes         TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO ai_short_watch (ticker, layer, severity_pct, deck_trend, deck_date) VALUES
  ('CRWV',  'neocloud',  -89, 'BEARISH', '2026-10-18'),
  ('NBIS',  'neocloud',  -89, 'BEARISH', '2026-10-18'),
  ('IREN',  'neocloud',  -89, 'BEARISH', '2026-10-18'),
  ('ORCL',  'oracle',    -79, 'BEARISH', '2026-10-18'),
  ('MU',    'memory',    -84, 'BEARISH', '2026-10-18'),
  ('SKHY',  'memory',    -84, 'BEARISH', '2026-10-18'),
  ('TSM',   'memory',    -84, 'BEARISH', '2026-10-18'),
  ('DLR',   'power',     -81, 'BEARISH', '2026-10-18'),
  ('CEG',   'power',     -81, 'BEARISH', '2026-10-18'),
  ('VST',   'power',     -81, 'BEARISH', '2026-10-18'),
  ('GEV',   'power',     -81, 'BEARISH', '2026-10-18'),
  ('EQIX',  'power',     -81, 'BEARISH', '2026-10-18'),
  ('AVGO',  'silicon',   -78, 'BEARISH', '2026-10-18'),
  ('ANET',  'silicon',   -78, 'BEARISH', '2026-10-18'),
  ('SMCI',  'silicon',   -78, 'BEARISH', '2026-10-18'),
  ('ASML',  'semicap',   NULL, 'BEARISH', '2026-10-18'),
  ('VRT',   'semicap',   NULL, 'BEARISH', '2026-10-18'),
  ('AMAT',  'semicap',   NULL, 'BEARISH', '2026-10-18'),
  ('LRCX',  'semicap',   NULL, 'BEARISH', '2026-10-18'),
  ('ETN',   'semicap',   NULL, 'BEARISH', '2026-10-18'),
  ('BX',    'financier', NULL, 'BEARISH', '2026-10-18'),
  ('SFTBY', 'financier', NULL, 'BEARISH', '2026-10-18'),
  ('ARCC',  'financier', NULL, 'BEARISH', '2026-10-18'),
  ('OWL',   'financier', NULL, 'BEARISH', '2026-10-18'),
  ('APO',   'financier', NULL, 'BEARISH', '2026-10-18'),
  ('ADBE',  'end customer', NULL, 'BEARISH', '2026-10-18')
ON CONFLICT (ticker) DO NOTHING;

-- Put every watch name in the screener universe (v_screener reads ticker_tags),
-- so live quotes, MFR coverage and the nightly TradingView run pick them up.
INSERT INTO ticker_tags (ticker, hedgeye_group, subsector)
SELECT ticker, 'AI bubble', layer FROM ai_short_watch
ON CONFLICT (ticker) DO NOTHING;

COMMIT;
