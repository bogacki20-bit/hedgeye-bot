# MyFractalRange (MFR) — practical reference

Source: myfractalrange.com/learn (14 guides, FAQ topics, /learn/scripts, /learn/ai), read 2026-11-15.
Written in our own words. "Unverified" means the in-app AI assistant said it but no Learn page backs it up.
The assistant warns that it can be wrong.

---

## 1. How the account works

### EOD ticker requests (the allowance)
- Each new ticker you reveal or open uses **one end-of-day (EOD) request**. Plan allowances are Free 3, Premium 50 and Elite 150, plus paid "extra tickers" add-ons (50 to 300/month). Kris's account shows **1,800**. On 11/15 the assistant said 757 had been used this month and 1,043 were left. The header "EOD" number is the **remaining** count.
- Deleting a ticker does **not** give its request back. Only a **Reset** does that.
- The 7 preloaded tickers (SPY, QQQ, GLD, USO, TLT, VIX, VVIX:VIX) are free. Crypto doesn't use the allowance, and the assistant says futures don't either.
- A ticker that fails to load (no provider data) is deactivated and its request is refunded. A ticker that won't load from search is never counted.
- The API docs talk about **monthly slots**. `quota_exhausted` means "no ticker slot left this month" and the fix is "a slot frees up next month". So the allowance behaves like a monthly quota.
- To see every ticker you have requested: user icon > Settings > Active Tickers (`/settings/tickers`).

### Resets
- A Reset swaps tickers you have deleted for fresh requests. To use one, first delete the unwanted tickers from Portfolio and Watchlists, then press Reset in Settings > Active tickers.
- Premium and Elite get 2 resets a month on the subscription anniversary. Unused resets never expire. Packs cost €5 for 1, €10 for 3 and €20 for 10. The header showed 28 resets.
- After a reset, the EOD count drops as you open your lists. Each ticker still in the account counts again as it is displayed. This is normal.

### Real-time tokens
- 1 token = a real-time refresh of one ticker's range, trend and momentum, priced off the current price. The Scanner costs **10 tokens per search**.
- Premium and Elite get 50 tokens a month, and they accumulate without expiring. Packs cost €10 for 100, €25 for 500 and €40 for 1,000. The header showed 800 RT tokens.
- A real-time refresh only works while the market is open (crypto works 24/7). Tickers with no real-time feed have a greyed-out checkbox.
- A real-time request made by *any* user updates that ticker intraday for everyone.

### When data refreshes
- EOD data is based on the **prior session's close**. All ranges, trend and momentum come from that close unless someone has paid for a real-time refresh.
- Refresh happens "between after-hours and pre-market". Providers don't deliver at a fixed time, so MFR says to **log in about 2 hours before the open** of the market you follow.
- Crypto: a daily bar from 00:00 UTC, refreshed between 00:00 and 01:00 UTC.
- The Recent changes tab is generated **6–7 am New York time**. It compares each ticker's signals at the close with the open, and its date is the day the change happened.
- Correlations update once a day, after the previous close, when the first user logs in.
- Pivots and targets: new ones come out on the first trading day of each month, quarter and year.
- Macro zones (futures) change only at each contract expiry.
- CFTC data is weekly.
- If price looks outside the range, that's because the range uses yesterday's close.

### Lists, portfolio and watchlists
- Lists come in two kinds, Portfolio and Watchlists, and both can be organised and named. The Learn tutorials for adding, organising, filtering, columns and export are **YouTube videos only**, with no text. We did not load them.
- Filters: trend, momentum, sector, country, U/D, daily performance, 3m and 1y Hurst, IV premium/discount, IV rank and percentile, and capped ranges.
- Display setting (Settings > Display settings): show "position in range %" instead of the U/D ratio.
- Alerts: Settings > Notifications and emails > "Alerts when a ticker in my lists crosses a plan level".
- Only one login is allowed at a time. Logging in on a new device logs out the previous one.

### Adding tickers in bulk
- **FAQ (official):** separate tickers with commas, and "we suggest 5 at a time at most".
- **Unverified (assistant):** you can paste up to 200 comma-separated tickers, or import a CSV through the same Add dialog. This contradicts the FAQ's advice, so test it with a small batch first.
- **API/MCP:** `mfr_add_watchlist_tickers` and `mfr_remove_watchlist_tickers` exist. The docs say *"Reading over MCP never activates a ticker or uses quota"*. They don't say whether *adding* through MCP activates a ticker and spends an EOD request. Assume it does, and test with one ticker before running any batch.
- Ticker formats:
  - FX pairs: `EURUSD`, `GBPEUR`.
  - Custom spread: `SPY:QQQ`, `GLD:SLV`. Indexes need a caret, e.g. `^GSPC:QQQ`, `^VIX`.
  - Crypto: `crypto.BTC`, `crypto.ETH`. Crypto needs the Crypto plan or Elite.
  - Futures: Elite only, about 20 fixed contracts (ES, NQ, DAX, Euro Stoxx, gold, oil, bonds, euro, BTC and others), with EOD symbols like `ES_F` and `NQ_F`. You can't add other futures.
- Coverage: US plus about 40 countries (CA, UK, FR, DE, IT, NL, JP, HK, CN, IN, AU, BR, MX and others). A ticker needs at least 2 months of history. After a split or de-SPAC it can take weeks to months before the ranges are reliable.

### Export of lists
- **Official:** lists export to **Excel and PDF**. The FAQ explains the export column abbreviations:
  - LFR = Lower Fractal Range
  - UFR = Upper Fractal Range
  - U/D = Upside/Downside ratio
  - Range % = position in range
  - LT = long term
  - 3m H / 1y H = 3-month / 1-year Hurst
- **Unverified (assistant):** CSV is also offered, and the file contains the list's visible columns: ticker, price, range edges, position %, LT range, Hurst and a legend. The "Choose your columns" tutorial suggests that what you see is what you export.
- For automation, `GET /v2/watchlist` and `/v2/asset` (see section 4) are the reliable route.

### Pairs and spreads
- Write a pair as `A:B` with a colon. It is a ratio series with its own range, trend and momentum. VVIX:VIX is one of the free tickers.
- Each spread uses one EOD request like any other ticker.

---

## 2. Metrics and indicators

### Fractal Range (short-term "Range") and Longer-Term range ("LT range")
- A proprietary low/high band, adjusted for stochastic volatility. It narrows when markets are calm and widens when they get nervous.
- Horizons: short-term is about 20 days or less (it follows "the last weeks"). LT is about 90 days or more ("the last months").
- How to read it: near the bottom, add to longs or cover shorts. Near the top, trim or build shorts.
- It is **not a forecast and not a stop**. When price leaves the range, the range moves with it. Use it with the trend: buying the low end works best in a bullish trend.
- On Premium and Elite, the LT range comes through the API as `ltRangeData`. Without it you get `ltLocked: true`.

### Position in range (Range %)
- 0% = bottom of the range, 100% = top. Example: 36% means lower half, more room up. 95% means lighten rather than add.

### Upside / Downside and U/D
- Upside = % distance to the top of the range. Downside = % distance to the bottom. U/D = upside ÷ downside.
- It comes for both the short-term and LT range. U/D above 1 means more room up. To add to longs, prefer a high U/D.
- U/D is **not a signal on its own**. Example: TLT had U/D 2.72 while the trend was bearish and all biases were down. Check the trend first.

### Relative Range
- The range spread divided by the last price. Compare it with its own average to see whether the range is widening or compressing.

### Caps (the "C" icon)
- Downside cap: the short-term and LT ranges share the same **bottom**, so a low may be in. Upside cap: they share the same **top**, so a top may be in. An upside cap is not the moment to start a long.
- Colour: the guides say **green C = downside cap, red C = upside cap, blue = both**.

### Trend
- Bullish, Neutral or Bearish. It comes from a proprietary formula on price, volume and volatility over **30–90 days**. It changes slowly on purpose.
- Prefer bullish trends for longs and bearish for shorts. Be more careful when it is Neutral.
- Shown as an arrow icon in tables.

### Momentum (last 30 closes)
| Colour / state | Rule |
|---|---|
| Green / Positive | Range top ≥ highest of the last 30 closes (buyers in control) |
| Red / Negative | Range bottom ≤ lowest of the last 30 closes (sellers in control) |
| Yellow / Neutral | Neither: consolidating |
| Blue / Volatile | Both at once: wide range of outcomes |

- Momentum changes far more often than the trend.
- Combining them: if the trend is bullish but momentum is negative, the trend may be pausing, so wait for momentum to turn before adding.

### Hurst exponent (3-month and 1-year)
- Scale 0–1. Below 0.5 = mean-reverting (fade the extremes). 0.5 = random walk. Above 0.5 = persistent or trending, and stronger as it nears 1 ("High Hurst").
- When trend and momentum agree *and* Hurst is high, follow the move.

### Pivots and targets (Elite)
- Published at the start of each **month, quarter and year** for about 1,200 tickers. They are built from the previous period's range plus the new period's open.
- Each period has 1 pivot and 4 targets: base upside, bull upside, base downside and bear downside. Three periods × 5 levels = the **15-level ladder** on the ticker's Plan tab. Each level shows its % distance from the last close, plus "next level above / below".
- Targets are zones where moves often pause, not price promises.
- Also available as the monthly targets file (Trading Plan tab), in the monthly Elite email, and as the TradingView "MFR Targets" script.

### Bias pills (M / Q / Y)
- Each pill shows whether the close is above (green ▲) or below (red ▼) that period's pivot.
- All three agree: clean trend, so hold with it and add on dips above the line to watch. Mixed (for example the year up but the month and quarter down): size down and watch.
- A bias flips **only on a daily close** beyond the pivot, never intraday. How far price sits from each pivot matters: 0.1% above means one close can flip it.

### Line to watch (URL slug `the-line-to-hold`)
- The single level that matters most for a ticker right now. It can be a pivot or a target, e.g. "monthly pivot" or "2nd monthly target".
- Shown on Today, in the "Line to watch" list column, and on the ticker page.
- Above it with a bullish trend: dips toward it are where to add. A daily close below it: trim, or cut if the next level down is far away. Back above it: the plan is back on. Closes only; wicks don't count.
- "Buying a ticker" playbook:
  - Enter near a level and buy in thirds.
  - Near a target above, trim ⅓ with the order just under the target.
  - On a close below the line: if the next level down is inside the range, trim half. If it is outside the range, exit.
  - If the next level above is outside the range, take a smaller position.

### Macro zones (futures only, Elite)
- An analyst sets a shaded "flatten zone" (its middle drawn as a solid line) plus 4 macro levels above (up1–up4) and 4 below (down1–down4). They are built from contract settlements and change only at expiry.
- Inside the zone, moves tend to flatten. A daily close beyond a macro level points to the next one.
- Zones are in **futures prices** and are never applied to the cash ETF (ES zones are not SPY levels).

### IV P/D (volatility premium or discount)
- Formula: (implied vol − realized vol) ÷ realized vol. Realized vol is 30 days.
- Expensive ≥ +3%. Fair is between −3% and +3%. Cheap ≤ −3%.
- The Volatility board also shows the value a day ago, a week ago ("pts 1w") and a month ago (the ring), plus a 60-session sparkline.

### IV rank and IV percentile
- IV rank: where today's IV sits between the past year's low (0) and high (100). It needs 253 sessions of history, otherwise it shows a dash. Values ≥ 90 or ≤ 10 are set in bold.
- IV percentile: the % of days in the past year when IV was lower than today. Above 75% = high vol for that asset. Below 25% = low.

### Gamma (Gamma page, and "Where dealers lean" on ticker pages)
- Source: the CBOE option chain. It covers SPY, QQQ, IWM, TLT and any optionable ticker.
- **Regime:** Damped when net gamma at the current price is positive, so dealers buy dips and sell rallies. Amplified when it is negative, so dealers hedge with the move. The number shown is net gamma in $ per 1% move.
- **Gamma flip:** where net gamma crosses zero.
- **Call wall:** the strike with the highest net gamma. **Put wall:** the strike with the lowest. Treat walls as zones and recheck them after big expiries.
- **Profile:** three lines (all expiries / without next expiry / without next monthly expiry). If the lines are far apart, the flip and walls can move after that expiry.
- The Gamma page brackets the MFR range next to the strike ladder.

### CFTC positioning
- Weekly data on large speculators (non-commercial traders) in 24 futures markets.
- The z-score compares the net position with its last **2 years**, and a 1-year z-score is also shown. Beyond ±2 the position is stretched long or short. That is context, not timing.
- Net shorts appear in brackets. "Latest" is green when net long and above its 1-year average, red when net short and below it.
- Compare z-scores across markets, never raw contract counts.
- CFTC data is **not available over MCP**.

### Correlations
- Pearson correlation of daily close-to-close returns. The site shows **10 and 21 sessions**. The matrix also offers 90 sessions and "change vs a month ago".
- A link is more trustworthy when it shows on both the 10- and 21-session windows.
- Each holding is compared with three anchors: SPY, TLT and UUP.
- Average correlation is the mean over all pairs on 21 sessions. When it rises, diversification is weaker.
- "What changed": Stronger, Weaker, or Flipped (the sign changed and both readings are at least 0.10 from zero).
- API windows: 5/10/15/20/30/60/90/120/180/252 days.

### Ticker page extras
- "Most watched by members" (top 20, with rank move vs 5–14 days ago), "How it moved" (day, week, month, quarter, year) and "Volume against its averages" (vs the prior day and 1M/3M/1Y averages; a move on low volume is easier to reverse).

### Scripts and indicators (`/learn/scripts`)
- Available on the MFR platform chart (Elite / Elite Plus). TradingView and Sierra Chart versions are invite-only, requested through Extras > Scripts and granted within about 48h.
- The list is:
  - **MFR:** MFR Range, MFR Trend, MFR Targets
  - **Vola2vola:** Volatility Trade & Trend, Daily volatility Trade & Trend, Buy the dip Daily, Buy & Sell signals on Daily, BTFD ES 3min chart
  - **Other:** Hurst Exponents combined, CorrMatrix (18 tickers), Vaidotas Momentum Score
  - MFR Range, MFR Trend and Vaidotas Momentum are also on Sierra Chart.
- **Learn publishes NO descriptions or formulas for any of these scripts.** The scripts page only lists their names. The in-app assistant confirmed this: no formula for the Trade or Trend line, no numeric thresholds for the green (calm) / red (stressed) colouring, and no trigger rules for Buy the dip or Buy & Sell.
- Our working definitions come from our own capture work: `docs/TV_LEVELS.md`, `docs/playbook/RULE_CARD.md`, and memory `mfr-trend-formula.md` for the MFR Trend band.
- To get MFR's own one-line meaning of each output: call MCP `mfr_list_indicators`, which returns each indicator's parameters, timeframes, output keys and one sentence per output. Then call `mfr_get_indicator`.
- Signal flags such as BTFD buy are 1 on a signal bar and 0 otherwise. Some scripts are daily only. `coverage: notCovered` means the script has rules only for certain symbols; it does not mean "no signal".

---

## 3. Scanner
- Searches every ticker in the database (100,000+, excluding crypto, which has its own Crypto tab filter) by the filter set in section 1.
- Cost: **10 RT tokens per search**.
- Scanner results are **not available over MCP**. `mfr_scan_indicator` is a different tool: it scans platform indicators over *your* tickers and costs 1 request per 5 tickers against the rate limit, not tokens.

---

## 4. API and MCP (beyond what we already knew)

### Plans and scope
- Creating a token needs Elite.
- A new token covers asset, preview asset, real-time asset, economic event, watchlist and MCP scopes.
- Tokens last 90 days, are shown only once, and stop working if the account loses Elite.
- A token from the test system is refused by production, and the other way round.

### Full MCP tool list
| Area | Tools |
|---|---|
| Assets | `mfr_get_asset`, `mfr_list_assets` |
| Real-time (1 token per ticker) | `mfr_get_realtime_asset`, `mfr_list_realtime_assets` |
| Watchlists | `mfr_list_watchlists`, `mfr_get_watchlist`, `mfr_add_watchlist_tickers`, `mfr_remove_watchlist_tickers` |
| Correlation | `mfr_get_correlation_to_benchmarks` (SPY/UUP/TLT), `mfr_get_correlation_matrix` (upper triangle only) |
| Calendar | `mfr_get_economic_calendar` (from/to ISO dates, max 92 days, default today + 7, country codes US/DE/EU/JP/UK) |
| Research (Elite) | `mfr_list_blog_posts` (title/excerpt only), `mfr_get_blog_post` (full text by id) |
| Platform indicators | `mfr_list_indicators`, `mfr_get_indicator`, `mfr_get_price_history`, `mfr_scan_indicator` |
| Gamma | `mfr_gamma` (summary by default; ask for the full strike curve explicitly, it is tens of KB; a past day by date) |
| Other | `mfr_get_today`, `mfr_catalog` |

- Not available over MCP: backtests, CFTC data and Scanner results.

### REST
- Auth: `Authorization: Bearer <token>`. The Excel Power Query example passes `&token=` in the query string instead; avoid that, because it leaks the token into logs.
- Endpoints:
  - `GET /v2/asset/GLD`: one ticker, which must be active.
  - `GET /v2/asset?idIn=TLT,SPY,GLD,^VIX`: several tickers.
  - `GET /v2/asset`: **every active ticker** in the account.
  - `GET /v2/rtasset/GLD` and `/v2/rtasset?idIn=...`: real-time, 1 token each.
  - `GET /v2/watchlist`: all watchlists.
  - `GET /v2/watchlist/<watchlist name Base64-encoded>`: one watchlist.
  - `GET /v2/asset/GLD?documentation=true`: the same answer with **every field explained**. Use this to map field names.

### Costs and limits
- Only real-time calls cost tokens. A failed call costs nothing.
- Rate limit: 150 requests per 15 minutes **per token**, shared by MCP and REST. Going over returns JSON-RPC error `-32029` with `retry_after_seconds`.
- A scan counts as ceil(n/5) requests and is refused up front if it would exceed the limit.

### Reading results
- `as_of` is the date of the newest data point. For futures it is the last daily close.
- `null` never means zero.
- Paged results continue while `pagination.truncated` is true.
- In batches, `coverage.returned` + `coverage.excluded` = `coverage.requested`. Each excluded ticker carries a code such as `ticker_not_active`.
- `entitled:false` comes with a reason: `notActive` or `quota`.

### Error codes
- Request and input errors: `invalid_argument` (unknown arguments are rejected, not ignored), `ticker_format`, `ticker_not_active`, `quota_exhausted`, `insufficient_subscription`, `not_found`, `ambiguous`, `window_too_large`, `too_many_tickers`, `benchmark_not_active`, `benchmark_unavailable`.
- Data errors: `realtime_unavailable`, `no_data`, `not_covered`, `still_computing`, `busy` (retry after 60 or 120 s; this is not "no signal").
- Limits and upstream: `rate_limited`, `upstream_timeout`, `upstream_error`, `internal_error` (retry once).
- Auth: `auth_missing`, `auth_invalid`, `auth_revoked`.
- Client and server: `tool_unavailable`, `method_not_allowed`, `not_acceptable` (406: the client must send `Accept: text/event-stream`).
- Real-time tools can return `effect: unknown` if a call broke off half-way. A token may have been spent, so check the balance instead of retrying blindly.

---

## 5. Gotchas for the bot
1. **Every new ticker costs a non-refundable EOD request.** Deleting a ticker doesn't refund it; only a Reset does. Never let automation "open" or "add" tickers casually. Use `/v2/asset` (all active tickers) or `idIn=` on tickers that are already active. Assume `mfr_add_watchlist_tickers` spends quota until a 1-ticker test shows otherwise.
2. **Inactive tickers fail quietly inside batches.** They land in `coverage.excluded` (`ticker_not_active` or `quota_exhausted`) and are not errors. Always reconcile requested against returned.
3. **EOD means the prior close.** Ranges, trend and momentum are computed off yesterday's close and refreshed somewhere between after-hours and pre-market, with no fixed time. Pull at least 2h before the open, around 7:30 AM ET for US names, and check `as_of` before trusting a row. Intraday, price can sit outside the "range" without that meaning anything.
4. **A real-time refresh costs 1 token per ticker** and fails outside market hours. When it fails, the reason (closed, no data or provider error) is never given. RT results can also show up because another user refreshed the ticker.
5. **Rate limit is 150 requests per 15 minutes per token**, shared between MCP and REST. Scans count ceil(n/5). Back off on `-32029` using `retry_after_seconds`.
6. **Tokens expire after 90 days.** A 401 `auth_invalid` saying "has expired" is the cue. Keep the token out of git and out of query strings.
7. **Ticker formats:**
   - Index: caret, e.g. `^VIX`, `^GSPC`.
   - Spread: colon, e.g. `SPY:QQQ`.
   - FX: `EURUSD`, with no `=X`.
   - Crypto: `crypto.BTC`.
   - Futures: EOD symbols like `ES_F` and `NQ_F`. They have **no indicators or price history** (`ticker_format`), and only about 20 house-chosen contracts exist.
   - International: about 40 countries are covered, but Learn never documents the suffix format. Check `mfr_catalog` or an existing list before assuming Yahoo-style suffixes.
8. **Pivots and biases flip only on daily closes.** New M/Q/Y levels appear on the first trading day of each period, so cached levels go stale on those days.
9. **Macro zones are in futures prices.** Never map ES zones onto SPY.
10. **Correlations and Recent changes are once-a-day snapshots.** Recent changes is built 6–7 am NY. Correlations are computed when the first user logs in after the close.
11. **New listings, splits and de-SPACs** need weeks to months of history before ranges are reliable. A ticker needs at least 2 months of history.
12. **One session per account.** A browser login on a new device logs out the old one. Token API calls should not be affected, but Chrome-based captures can be kicked out.
13. **Vola2vola formulas are unpublished.** Don't present Trade, Trend or BTFD definitions as MFR-official. Use `mfr_list_indicators` output sentences or our own TV capture notes.

## Coverage found by search (11/15, search box only, no requests spent)
- Futures use `XX_F`. Already active: grains ZC/ZS/ZW/ZL/ZM, energy CL/NG/HO/RB, metals GC/SI/HG/PL/PA, US rates ZT/ZF/ZN/ZB/UB/ZQ, Eurex FGBS/FGBM/FGBL/FGBX/FOAT/FBTP/FBTS, index futures.
- Softs and livestock have NO `_F` continuous future (KC_F, SB_F, CC_F, CT_F, OJ_F, LE_F, HE_F, KE_F, BZ_F all return nothing). Use the Bloomberg subindexes: ^BCOMKC coffee, ^BCOMSB sugar, ^BCOMCC cocoa, ^BCOMCT cotton, ^BCOMLC live cattle, ^BCOMLH lean hogs, ^BCOMKW KC wheat, ^BCOMCO Brent. ETFs: CANE, WEAT, CORN, SOYB, JO, DBA, DBC, BNO. Orange juice: none found.
- Foreign stocks are searchable with Yahoo-style suffixes: SAP.DE, ASML.AS, NESN.SW, 7203.T, 005930.KS, 2330.TW, MC.PA, NOVO-B.CO, SHEL.L, XBB.TO, IAF.AX. Some listings show MIC suffixes (.XNSE, .BVMF, .XMIL, .XETRA, .XLON); check the exact symbol in search before importing.
- Foreign government bond ETFs: IGLT.L / VGOV.L (UK gilts), EXX6.DE (Germany), IBGL.L (euro govt 15y+), XGB.TO / ZFL.TO (Canada), IAF.AX (Australia), CGB (China), EBBETF0430 (India, .XNSE), IMAB11 (Brazil linkers, .BVMF), IGIL.L (global linkers). Japan: not found yet.
- Foreign junk/EM: IHYG.L / EUNW.DE (euro HY), IEMB.L / VDET.L (EM USD sovereigns), EMHC.
- Not verified yet: whether ^BCOM indexes and foreign listings get indicators (Vola/Trend) as well as ranges. The MCP docs say futures `_F` have no indicators or price history. Test with 1-3 names before a big import.
- Proposed lists: data/mfr_add_candidates.csv (170 US/inventory names), data/mfr_global_proposal.csv (108 global names).

## Test reveals (11/15, Kris approved 3)
- Opening a ticker page is free. MFR shows a "Reveal <ticker> · 1 ticker request" button, and only that click spends a request. A page that says "No data for X" costs nothing.
- SAP.DE: full set (trend, momentum, ranges, M/Q/Y plan, Vola Trade 207.31 / Trend 195.55, MFR Trend cloud 192.43–199.38). No options IV (foreign).
- IGLT.L and COCO.L (London): full set, but the indicator lines (Vola Trade/Trend, MFR Trend, targets) are blank on the latest bar and populated one bar back (COCO.L 11/12: Trade 8.70, Trend 8.76, cloud 8.96–9.46). Treat London lines as one day behind; ranges and trend tiles are current.
- ^BCOMKC (Bloomberg coffee subindex): "No data". The ^BCOM subindexes are not usable, so use the WisdomTree London ETCs for softs: COCO.L cocoa, COFF.L coffee, COTN.L cotton (sugar and livestock not found yet).
- Counter after the tests: 1,040 of 1,800 left, 760 active tickers, 28 resets (next on 23 Nov). The header number can briefly differ from Settings; Settings → Tickers and resets is the truth.

## Import done (11/15)
- New MFR lists: "Global" (101 names) and "Desk adds" (150 names incl. CART, POWA added by hand).
- Batch import rejects some symbols it can't match ("not tickers we know"), but the same names can exist in search; add those one at a time from the Add box and check the suggestion (ROKU picked R35.F Frankfurt by mistake).
- Rejected or no data: VGOV.L, EBBETF0430 (both forms), IMAB11 (both forms), ROG.SW, JO, ORCS, SKHY (use 000660.KS), ATZ, FBY, FDXF, GLAS, GRGDF, GTM, IRVH, LTMC.MI, OFRM, OPTU, PNDRY, PSKY, RPBPF, SKYD, SSNLF, YSWY. In the list but "No data": FM, HYXU, FI, FYBR, GLASF, ROKU, R35.F.
- Opening My lists shows the first list (6/24/26) and re-activates its names; that cost about 30 requests the first time this period.
- Account after the import: 773 of 1,800 left, 1,027 active.
- The bot's nightly sync (main.py _mfr_watchlist_loop, 00:02 UTC) reads every active ticker, so the new names flow into mfr_snapshots automatically from tonight.

## Indicator map: what each one measures (11/15)
From the platform's signals feed (ticker page, Elite) and MFR's own API docs (`?documentation=true`).

| Feed | Fields | What it measures | Status for us |
|---|---|---|---|
| VOLA_TT (Volatility Trade & Trend) | trade, trend, bear (1 = stressed/red) | Vola2vola Trade and Trend lines on price; green while vol is calm, red while stressed | Trade = our card's Trade line (matches TV to the cent). Pull nightly. |
| MFR_TREND (Myfractalrange Trend) | bull (top), bear (bottom) | The yellow cloud: bullish above, neutral inside, bearish below | = our card's Trend (bottom for longs, top for shorts). Pull nightly. |
| MFR_TARGETS | up1/up2/dn1/dn2 + open (pivot) for M, Q, Y | Monthly/quarterly/yearly pivots and targets; the monthly pivot is the "line to watch" | Track. Candidate trim/entry levels after review. |
| VOLA (Volatility indicator) | vixfix, trade, trend, trendDown, hi, lo | Williams VIX Fix: a synthetic "fear gauge" built from price, with its own trade/trend lines | Track only. |
| MATRIX (Simplified Matrix Series + VixFix) | wvf, wvfHot, bull, res, sup, upX, downX | Trend candles, overbought/oversold marks, vol spikes | Track only. |
| BS_SIGNALS | buy, megaBuy, sell, megaSell | Potential bottoms and tops | Track only; score forward returns. |
| BTFD (Buy the dip) | buy | Marks potential capitulation days | Track only; compare to our 3-day-selloff long entry. |
| MOMENTUM (Vaidotas score, 10/30/90) | p1, p2, p3 | How stretched a move is over 10, 30, 90 days | Track only. |
| REST momentumSignal | bullish / bearish / neutral / neutralDanger | Short-term range vs the 30-day close band (above the max close = bullish, below the min = bearish, both = danger, neither = consolidation) | Already stored nightly (mfr_snapshots). |
| REST trendSignal, hurst, hurst3Mo | | Trend state; Hurst > 0.5 trending, < 0.5 mean-reverting | Already stored. |
| REST iv, rv, ivpd, rvStdev30 | | 30-day IV; 30-day realized vol (annualized); IV premium = iv/rv − 1; vol of vol | iv/rv stored; ivpd and rvStdev30 are in full_payload. Add columns. |
| REST gammaMetrics | zeroGamma, callWall, putWall, absoluteGamma, strike curve | Dealer gamma map (same idea as SpotGamma) | Already stored (call_wall_mfr, put_wall_mfr, zero_gamma). Compare with SpotGamma. |

**Rule (Kris 11/15):** the new indicators are tracked and scored before any of them enters the process. Nothing goes on the card without his ruling.

**Tracking plan:**
1. Nightly pull of the signals feed for every active ticker into a new table `mfr_indicators` (one row per ticker per day), once an MCP-scoped token is in Railway as `MFR_MCP_TOKEN`.
2. Backfill: the feed carries about 2,000 days of history per ticker, so each signal can be scored on years of data on day one, not just going forward.
3. Scorecard (weekly, with the Saturday review): for each event (BS buy/sell/mega, BTFD, momentum state flips, VOLA_TT calm→stressed flips, Matrix overbought/oversold, price crossing M/Q/Y pivots), the forward 5/10/21-day return and hit rate vs the base rate. Split by quad, by trend state and by book vs universe.
4. Bring the scorecard to the monthly rule review (first one 11/28–29). Kris decides what, if anything, joins the card.

## Indicator pipeline (built 11/15)
- Token: `MFR_MCP_TOKEN` in Railway (created 11/15, expires ~2/13/2027, 90 days). The old `MFR_API_TOKEN` stays for the REST nightly sync.
- Table: `mfr_indicators` (migration 137), one row per ticker per daily bar; view `v_mfr_indicators_latest`.
  Core: vola_trade / vola_trend / vola_bear (Vola2vola Trade & Trend), trend_bull / trend_bear (MFR Trend = the cloud: bear = bottom for longs, bull = top for shorts), tm_/tq_/ty_ open/up1/up2/dn1/dn2 (M/Q/Y targets).
  Track: bs_buy / bs_mega_buy / bs_sell / bs_mega_sell, btfd_buy, mom_p1-3, vixfix + vix_trade/trend/trend_down/hi/lo, mx_* (Matrix).
- Puller: `py -m tools.mfr_indicators --group core|track|all --bars N [--tickers A,B] [--dry-run] [--start-after T]`. One request = 5 indicators for one ticker; paced at 6.2 s (150 per 15 min). A full pass is about 1,040 requests (~1.8 h) per group. It never spends ticker quota.
- Nightly: main.py `_mfr_indicators_loop` at 06:00 UTC (~1 AM ET), Tue–Sat, core then track, bars=5. Runs on Railway only after the code is deployed.
- Backfill: run 11/15 with bars=500 (about 2 years) for every active ticker; logs in data/mfr/backfill_core.log and backfill_track.log.
- Futures (`_F`) return no indicators (MFR documents this). Their ranges still come from the REST sync.
- MCP gives London names the current bar (11/13 for COCO.L); the one-day lag is only on the website chart legend.
- Scorecard: `py -m tools.mfr_signal_scorecard [--since 2024-11-01]` writes data/reviews/mfr_signals_<date>.md: hit%, average and edge vs base rate at 5/10/21 days, split by trend state. Tracking only until Kris rules.
- First scorecard (11/15, bars since 11/1/2025, ~140k events, data/reviews/mfr_signals_2026-11-16.md): only two reads beat the base rate at 21 days, both by trend state. BS buy below the band: n 10,370, 61.5% up, edge +1.4%. BS sell above the band: n 8,438, edge +1.1% (a trim-strength read, most still rose). BS buy inside/above the band lost (-3% to -14% edge). WVF hot, vola calm/stress, momentum extremes and monthly-pivot crosses are within ±1% of base: noise. BTFD fired 7 times, too few. Re-run monthly.
- Momentum added (11/15, Kris "score them"; data/reviews/mfr_signals_momentum_2026-11-15.md; entry days only; the indicators table holds about one year of signal bars, the snapshot momentum signal only 5/2026 on). Edge at 21 days vs the same names' base rate, by where price sits against the band:
  - Buy-type, below the band: 30-day momentum turns up (mom2_up0) n 4,112, +2.8% (+1.1% at 5d); 30-day stretched low (mom2_lo, under its 5th pct) n 2,526, +2.3%, 64% up; snapshot signal flips bullish n 691, +1.5%.
  - Sell-type, above the band: snapshot signal flips bearish n 423, +4.8% (63% fell); flips to neutral-danger n 382, +3.7%; 90-day momentum stretched high (mom3_hi) n 1,417, +2.2%; 10-day or 90-day momentum turns negative +2.3%.
  - Losers: momentum all positive above the band (chasing) −1.8%; snapshot bullish flip above/inside the band −3.3%; momentum all negative below the band as a short −1.5% (they bounce).
  - Read: the reversion pattern is relative to the band (buy weakness below it, trim strength above it), which is how the card already works. The snapshot-signal rows cover about 6 months of one regime.
- **Line source since 11/15 (Kris "Yes, switch it"):** the card's Trade and Trend cloud read MFR first, TradingView second, via view `v_line_source` (migration 140): newest bar wins, tie to MFR; MFR's `USD` row is the 2x semis ETF and is keyed USD_SEMI to match tv_levels. Readers: v_screener (adds `line_source`), level_lines lookup (labels "(MFR)"/"(TV)"), desk_snapshot reviewer pack. Before/after on 801 screener rows: 702 MFR, 92 TV; Trade within 0.1% on 781 of 787; it fixed USD's cloud, which had read the dollar index.

## Correlation tools (found 11/15, no ticker cost)

Every ticker must be active in the account. Each call counts as 1 request against the 150 per 15 min limit.

- `mfr_get_correlation_to_benchmarks`: up to 25 tickers against up to 5 benchmarks, up to 4 windows (5–252 trading days), `as_of_days_ago` for history. Pearson on daily returns, with the two series aligned by date.
- `mfr_get_correlation_matrix`: 2–15 tickers, every pair (upper triangle) plus a summary (mean, highest, lowest). One window per call.
- `AVG_SECTOR_CORR` (via `mfr_get_indicator` on SPY): the average correlation of the 11 sectors to SPY. High = the market moves as one block; low = the sectors go their own way. 250 bars per call.
- `CORR_MATRIX` indicator: one ticker against 18 fixed ETFs (DIA, GLD, IWM, QQQ, SPY, TLT, UUP, the 11 sector SPDRs).
- `mfr_get_today`: the major pair correlations (GLD/SPY, SPY/UUP, GLD/UUP, SPY/TLT) over 10 and 21 days with a trend word, plus 21-day sector-correlation history.

First read, book of 11/14 (84 names; ILS, ORCS and SKYDWS not covered). Book correlation-weighted by market value:

- SPY: +0.05 (30 days) / +0.03 (90 days).
- TLT: +0.36 / +0.28. The book trades the rates story.
- UUP: −0.23 / −0.04.
- USO: −0.15 / −0.18.

Top-15 pairwise mean is 0.21. The most redundant pairs are XLP–YUM 0.74 and BNDD–TLT 0.74.

Average sector correlation was 0.68 (10-day) on 11/13, the 98th percentile of the past year. It had been about 0.0 on 11/3 and rose sharply from 11/4.

Nightly since 11/15 (Kris yes): `tools/mfr_correlations.py` runs after the indicator pull, writes mfr_sector_corr, mfr_pair_corr, mfr_book_corr, mfr_book_pairs (migration 138), summary view v_mfr_book_corr_summary. About 10 requests. Sector series backfilled one year.
