# TradingView TRADE / TREND levels — overnight capture

**Rule (Kris, 2026-09-26):** the bot's replication of Keith's Alpha Code is a blend:
- **TREND = MFR Trend (Myfractalrange Trend) daily band**: lower edge for
  bullish names, upper edge for bearish.
- **TRADE = Vola2vola volatility Trade & Trend daily line**: upper line for
  bullish names, lower for bearish.

Tested against 11 of Keith's published levels (SPX, Gold, Bitcoin, WTI, Brent):
TREND exact on SPX (7501 vs 7501.67) and Gold. TRADE exact on SPX (7651 vs 7650.61).
Brent 97/91 matched neither. **Bearish is an untested mirror**: cards show
`·mir` / `*` until the Alpha Code scorecard confirms it.

Neither value is in MFR's API (the API has only the short/long-term ranges = the
blue/pink "Ranges" lines). Both are read from TradingView in **Kris's Chrome**.

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
