# TradingView TRADE / TREND levels — overnight capture

**Superseded 11/2/2026:** TRADE = 34-day EMA and TREND = 89-day EMA (`tools/level_lines.py`, `level_lines` table, migration 125). `v_screener.tv_trade` / `tv_trend` read those. TradingView now supplies the ranges and is the fallback for names without an EMA. MFR Trend band, solved 11/2: mid of 63-day high/low ± mid × population stdev of 63-day log returns.

**Old rule (Kris, 2026-09-26):** the bot's replication of Keith's Alpha Code was a blend:
- **TREND = MFR Trend (Myfractalrange Trend) daily band**: lower edge for
  bullish names, upper edge for bearish.
- **TRADE = Vola2vola volatility Trade & Trend daily line titled "Trade"** (`vola_b`, the
  curve) on every name (Kris, 11/2). `vola_a`, the step line, is the indicator's own Trend, unused.

Tested against 11 of Keith's published levels (SPX, Gold, Bitcoin, WTI, Brent):
TREND exact on SPX (7501 vs 7501.67) and Gold. TRADE exact on SPX (7651 vs 7650.61).
Brent 97/91 matched neither. **Bearish is an untested mirror**: cards show
`·mir` / `*` until the Alpha Code scorecard confirms it.

Neither value is in MFR's API (the API has only the short/long-term ranges = the
blue/pink "Ranges" lines). Both are read from TradingView in **Kris's Chrome**.

## Line source: MFR first since 11/15/2026

Kris switched the Trade / Trend-cloud source to MFR (`mfr_indicators`), with TradingView as the backup.
View `v_line_source` (migration 140) picks per ticker the newest bar of either; a tie goes to MFR.
`v_screener` (tv_trade, tv_mfr_upper/lower, tv_vola_b, new `line_source`), `tools/level_lines.py lookup`
and the reviewer pack read it. Checked 11/15: Trade within 0.1% on 691 of 697 names, cloud 729 of 732.
The TradingView run stays full: it's the backup, the only source for futures / foreign yields / FX /
index codes and ~38 stocks MFR lacks, and it still feeds tv_levels ranges and closes
(morning_brief, country_quads, macro_sunday, weekly_scorecard read tv_levels directly).

## Where it shows up
`v_screener.tv_trade / tv_trend / tv_basis` (migration 117), so every screen gets it:
MARKET / MARKET FULL / MARKET <ETF> (`TRD x TND y` on every line), SCREEN rows,
BOOK RP (px · TRADE · TREND columns), `RP <ticker>`, and the desk-note thesis cards.
Formatter: `tools/tt_levels.py`.

## Nightly run (scheduled task `tv-levels-overnight`, 00:05 ET weekdays)
1. Open the bot's TradingView layout in Kris's Chrome (Claude-in-Chrome). It must
   carry three indicators: "Myfractalrange Trend", "Vola2vola volatility Trade & Trend"
   (not saved in the layout: add it from Indicators → search "Vola2vola", leave
   the layout UNSAVED) and "MFR Range" (legend "Ranges - D": st/lt range lines,
   stored as tv_levels.range_* — the screener's last-resort range tier, 9/29).
2. `py -m tools.tv_levels --pairs` → JSON of `[bot_ticker, tv_symbol]` (~755).
3. Inject `tools/tv_levels.js` (javascript_tool), then `__tvStart(pairs)`.
   ~8-10 s/symbol with MFR Range (it computes slowly) ≈ 2 h for ~750 names. Waits run on a Web Worker timer, because Chrome
   throttles hidden-tab timers to ~1/min after 5 min.
4. Poll `__tvPeek(from, 25)` in chunks of 25 (tool output truncates long
   strings) and append each row as a JSON line to `data/tv_levels/<date>.jsonl`.
5. `py -m tools.tv_levels --ingest data/tv_levels/<date>.jsonl` → upserts
   `tv_levels` + `tv_levels_misses`; report stored/misses.
6. Never save the layout; never enter passwords; leave the tab open.

Values come from the chart engine (`TradingViewApi.activeChart()._chartWidget
.model().model().dataSources()` → `.data().each`), so the tab doesn't need to be
visible or drawing. Rows: MFR Trend = `[t, upper, lower]`, Vola = `[t, a, _, b]`.
Last **completed** daily bar: stocks/funds/indices take the last bar; futures/FX/
crypto/bonds skip a session that opened < 20 h ago. Session dates are stamped at
bar time + 6 h, so a 22:00 UTC futures open belongs to the next day.

## Prices: Yahoo, not MFR (MARKET ticket 9/25)
`tools/quotes.py` → `live_quotes` + `quote_snapshots` (migration 117/118).
Railway's `main.py` refreshes the whole universe every 5 min in market hours plus
one post-close pass (`LIVE_QUOTES_ENABLED`). `v_screener.price` = the newer of the
Yahoo quote and MFR's snapshot, and range_pos is recomputed from it. MARKET also
pulls fresh quotes for every name it prints (corpus-first), prints `@HH:MM` ET,
and flags `⚠stale-px` (failed quote / unchanged vs last run while the sector ETF
moved ≥0.10% / dead feed 3+ sessions). Stale names never enter ZONES.

## Ranges second pass (11/11/2026)
MFR Range stopped landing on 11/9 (bar 11/9: 60 of 935 rows had ranges; bar 11/10: 93 of 904).
Cause: in the long-lived capture tab the study sat in status 1 (computing, 0 rows) on every
symbol. In a **freshly loaded** chart tab it computed normally (ABNB: 400 rows, 4 lines,
status 2), so it is not an access problem. tv_levels.js now waits only ~3 s for ranges in
the main pass, so every night needs a second pass:
1. After the main pass is ingested, reload the chart tab (or open the layout in a new tab;
   never save the layout). Re-inject tools/tv_levels.js.
2. `py -m tools.tv_levels --range-pairs` -> names with band_source 'tv'/null and no range
   on the latest bar, book names first.
3. `__tvStart(pairs, {rangesOnly: true, waitK: 75})` (30 s range wait per symbol;
   about 20-30 s each, so ~120 names = ~50 min). Collect with `__tvPeek` as usual.
4. `py -m tools.tv_levels --ingest-ranges <file>.jsonl` fills range_* only and never
   touches TRADE/TREND (plain `--ingest` would null them on ranges-only rows).
`py -m tools.feed_health` now has a "TradingView MFR ranges" line (STALE under 80%).
