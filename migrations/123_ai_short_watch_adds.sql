-- 123_ai_short_watch_adds.sql — Kris 2026-10-19.
-- AMZN and GOOGL join the AI short watch: the 10/18 deck had them on the
-- BULLISH side, but the bot's trend flipped BEARISH after it and Signal
-- Strength dropped both 10/16. deck_trend stays NULL (the alert reads the
-- live v_screener trend). AMD and ASX (deck bullish names) join the
-- universe so all 36 deck names are priced and leveled.
BEGIN;

INSERT INTO ai_short_watch (ticker, layer, severity_pct, deck_trend, deck_date, notes) VALUES
  ('AMZN',  'hyperscaler', -60, NULL, '2026-10-18', 'deck BULLISH; bot trend BEARISH after deck; off SS 10/16'),
  ('GOOGL', 'hyperscaler', -60, NULL, '2026-10-18', 'deck BULLISH; bot trend BEARISH after deck; off SS 10/16')
ON CONFLICT (ticker) DO NOTHING;

INSERT INTO ticker_tags (ticker, hedgeye_group, subsector) VALUES
  ('AMD', 'AI bubble', 'silicon'),
  ('ASX', 'AI bubble', 'foundry')
ON CONFLICT (ticker) DO NOTHING;

COMMIT;
