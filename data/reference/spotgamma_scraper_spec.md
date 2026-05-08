# SpotGamma Scraper — Comprehensive Spec

**Status:** spec rev 2 — written 2026-05-06 evening, expanded 2026-05-07 early morning after deeper walkthrough revealed the 4,922-symbol universe table and full Scanner dimension list. This is the blueprint for the production scraper.

**Goal:** capture EVERYTHING SpotGamma Essential exposes — not a curated subset. Disk is free; the corpus has more value with breadth than with selectivity. Kristian's stated direction: "we want to get everything."

**What's NOT in scope:** the Alpha-tier locked features Kristian doesn't pay for (HIRO, Volatility Dashboard, TRACE). If those tiers get added later, expand the spec.

---

## The big find: the 4,922-symbol master table

**Location:** bottom of any Equity Hub page (`/equityhub?sym={ANY}`). Section labeled "Equities" → "SpotGamma Key Daily Levels."

**Default columns (12):** Symbol, Current Price, Previous Close, Stock Volume, 52 Week High, 52 Week Low, Earnings Date, Key Gamma Strike, Key Delta Strike, Hedge Wall, Call Wall, Put Wall.

**Configurable columns** via the Columns button. Likely to include the full Scanner dimension list (see below) — this is the priority for the first scrape: configure all available columns on, then capture.

**Total rows:** 4,922 symbols (entire SpotGamma universe).

**Export:** there's an Export button — likely CSV download. If yes, this is the cleanest scrape: trigger Export, save the CSV, parse to markdown + Postgres. If no, scrape the rendered table HTML.

**This single capture replaces per-ticker Equity Hub navigation for the breadth case.** Before this find, I was planning ~40 per-ticker scrapes. Now it's one master pull for the universe + per-ticker deep-dives only when we want sub-tab data (Skew, Risk Reversal, etc.) for tier-1 names.

---

## Scanner dimensions (per-stock metrics across the universe)

The Scanner page (`/scanners?eh-model=legacy`) Explorer View dropdowns reveal the full list of per-stock metrics SpotGamma tracks. Confirmed dimensions visible in the X dropdown (more below the cutoff):

- IV Percentile
- 1 Month Implied Vol
- 1 Month Realized Vol
- Next Exp Delta %
- Next Exp Gamma %
- Next Exp Call Volume %
- Next Exp Put Volume %
- Proximity to Call Wall
- Proximity to Put Wall
- Garch Rank
- IV Rank
- Risk Reversal Rank
- Risk Reversal Percentile
- Call Skew Percentile
- (more below the visible cutoff — verify on Friday's build)

These are all per-stock per-day. Adding them to the master table via the Columns button gives ~25+ metrics × 4,922 stocks = enormous corpus per day, all in one capture.

---

## Pages on Essential tier (full map)

### 1. Market Overview — `/home?eh-model=legacy`

Index dashboards for SPX/SPY/NDX/QQQ/RUT/IWM. Default landing page.

**Tabs (top):** S&P ES=F Live Prices, SPX Gamma Model, SPX Combo Strikes, SPX Absolute Gamma.

**Bottom subsections:** Index Levels, SpotGamma Levels, Index Metrics, Support & Resistance.

**Captured data:** intraday chart annotations (Call Wall, Implied Move High/Low, Last Closing) per index; comprehensive level tables.

### 2. Founder's Notes — `/foundersNotes`

Two notes per trading day: morning "Founder's Note" (~7-8 AM ET) and afternoon "PM Note" (~5 PM ET). Each contains:

- Tape header (^SPX, ^NDX, ^VIX, WTI, Gold)
- "Key dates ahead" upcoming events
- "SG Summary" running journal — newest at top, older entries struck through
- "Key SG levels for SPX" — Resistance, Pivot (with directional bias), Support
- PM commentary: market action narrative + flow analysis (HIRO, gamma concentrations, TRACE flips)
- Single-name flow callouts
- **Comprehensive level table per /ESM26, SPX, SPY, NDX, QQQ, RUT, IWM:** Reference Price, SG Gamma Index™, SG Implied 1-Day Move, SG Implied 5-Day Move, SG Implied 1-Day Move High/Low, SG Volatility Trigger™, Absolute Gamma Strike, Call Wall, Put Wall, Zero Gamma Level, Gamma Tilt, Gamma Notional, 25 Delta Risk Reversal, Call/Put Volume, Call/Put Open Interest
- Key Support & Resistance Strikes per index/ETF
- Combo strikes with confidence percentages

### 3. Reports → FlowPatrol — `/reports`

Confirmed: Categories filter shows only "FlowPatrol" on Essential. No other report types on this tier.

19-page daily institutional flow research report. Sections:

- Executive Summary (5 bullet observations)
- Index ETF Positioning (narrative + position changes table)
- Single Stock Positioning (narrative + table)
- Sector Breakdown (narrative + ETF table)
- Directional Positioning
- **Top Position Changes by Delta** (large table — Stock, Strike, Type, Exp, $ Delta Chg, Stock Px, IV, BTO/BTC/STO/STC, Open Int, 1D/5D Return, Sector, Spread ID)
- Gamma Positioning (narrative + Top Position Changes by Gamma table)
- Volatility Positioning (narrative + Top Position Changes by Vega table)
- Largest Premium Trades
- Largest Index Trades / Top Index Trades
- Unusual Options Positions
- **Statistically Significant Positions** (per-stock greek percentile table)
- Sector Statistical Analysis
- Heavy DayTrading/Algo Flow (top names by volume / synthetic-OI ratio)

### 4. Equity Hub — `/equityhub?sym={TICKER}&tab={TAB}`

Per-ticker option positioning dashboard with **9 sub-tabs**:

1. `composite-view` (default) — gamma chart with Hedge Wall, Key Gamma Strike, Call Wall, Put Wall, Last Closing
2. `put-call-impact` — call/put gamma decomposition
3. `live-price-sg-levels` — intraday chart with all levels overlaid
4. `skew` — IV by delta (next expiration + 30d, with prior-day overlay)
5. `history` — historical levels
6. `risk-reversal` — 25-delta risk reversal data
7. `fixed-strike-matrix` — fixed-strike vol surface
8. `term-structure` — IV term structure across expirations
9. `volatility-skew` — additional skew analytics

**Right panel data fields per ticker (FULL list, expanded with "Show more"):**

Current Price, Daily Change, Previous Close, Earnings Date, Call Wall, Put Wall, Skew Rank, IV Rank, Call Gamma, Put Gamma, Top Gamma Exp, Top Delta Exp, Call Volume, Put Volume, **Put/Call OI Ratio, 1 M RV (realized vol), 1 M IV (implied vol), Garch Rank, Options Implied Move ($)**, Largest Gamma Strike (date), Largest Delta Strike (date), Gamma Hedge Est.

**Bottom of page:** the 4,922-symbol master table described above.

### 5. Indices — `/indices?sym={SYM}`

Per-index dashboards. Tabs across top: SPX/SPY/NDX/QQQ/RUT/IWM/Personal View. Each has 3 sub-tabs:

**Greeks:** SPX Gamma Model, SPX Delta Model, SPX Vanna Model, SPX Absolute Gamma, SPX Combo Strikes (each with multi-day overlay).

**Volatility:** SPX SIV Index, SPX Options Risk Reversal (years of historical data back to 2020), SPX Price vs 2M/6M Realized Volatility, SPX 5 Day & 1 Month Return Histogram.

**Open Interest:** TBD — verify on Friday's first scrape, but presumably OI-by-strike charts and breakdowns.

### 6. Tape — `/tape?eh-model=legacy`

Live options tape. Top tables:

- TOP OPTIONS VOLUME — ticker, volume (top 5)
- TOP DAILY GAMMA NOTIONAL — ticker, $ notional (top 5)
- TOP DAILY MOVERS — ticker, last close, price, change %
- LARGEST DAILY TRADES — ticker, premium, expiry, strike, type

**Pie charts:** VOLUME (puts vs calls), PREMIUM ($ split), DELTA ($ split).

**Below:** filterable Flow Data / Contract Data table — full options trade tape with filters (timeframe, ticker, watchlist, scanner).

### 7. Scanners — `/scanners?eh-model=legacy`

IV Rank vs Risk Reversal Rank "Compass" view. Tabs: **Compass**, **Guided View**, **Explorer View**.

**Explorer View** lets you pick X/Y/Z dimensions from the full Scanner dimension list (above) and plot the universe. With "Show All" loaded, gives 4,922-stock scatter on any 2-3 dimensions.

For daily scrape: capture the master table from Equity Hub (which uses these same dimensions as columns) rather than navigating Scanner's plot UI.

### 8. Options Calculator — `/options-calculator` (NEW)

Trade simulator. NOT corpus-relevant — used live, not scraped.

### 9. Locked tiers (don't have)

- HIRO — `/hiro` (returns AAPL demo for non-subscribers)
- Volatility Dashboard — `/ivol`
- TRACE — separate tier

URLs documented for future upgrade.

---

## Recommended scrape schedule

Three scheduled tasks on the new laptop, all driving Claude in Chrome:

| Job                          | Cron               | Captures |
|------------------------------|--------------------|---------|
| **Pre-market sweep**         | `15 6 * * 1-5`     | Founder's Note (when published) → Market Overview → Tape top tables → Indices (all 6, all 3 sub-tabs) → **Master table CSV export with all columns** → Per-ticker deep-dive on tier-1 names (alert universe, ~40 tickers) covering all 9 Equity Hub sub-tabs |
| **Post-close sweep**         | `15 17 * * 1-5`    | PM Note → FlowPatrol full report → Master table CSV (closing values) → Tape post-close summary → Per-ticker tier-1 closing levels |
| **Hourly during market hours** | `0 10-16 * * 1-5` | Light pass: Market Overview intraday levels, Tape top tables, Master table CSV (intraday snapshot — captures level shifts during the session) |

That's ~9-12 runs per trading day. Heavy at open/close, lighter intraday.

**The hourly master table capture is the corpus engine.** ~5,000 symbols × 25+ metrics × 7 hourly snapshots × 250 trading days = **~2 million data points per day, ~500 million per year**. ML training data at scale.

---

## Output structure (file system)

```
data/snapshots/spotgamma/YYYY-MM-DD/
├── market_overview.md                    (tape + index levels)
├── founders_note_am.md                   (pre-market note)
├── founders_note_pm.md                   (post-close note)
├── flowpatrol.md                         (daily flow report — full text)
├── tape.md                               (top tables + pie summaries)
├── indices/
│   ├── SPX_greeks.md
│   ├── SPX_volatility.md
│   ├── SPX_open_interest.md
│   ├── SPY_greeks.md
│   └── ... (6 indices × 3 sub-tabs = 18 files)
├── equityhub_master/
│   ├── HH00.csv                          (hourly export, e.g. 0700.csv)
│   ├── 1000.csv
│   ├── 1100.csv
│   ├── ... (one per hourly capture during market hours)
│   ├── 1600.csv
│   └── eod.csv                           (post-close authoritative)
└── equityhub_deep/
    ├── HYG/
    │   ├── composite_view.md
    │   ├── put_call_impact.md
    │   ├── live_price_sg_levels.md
    │   ├── skew.md
    │   ├── history.md
    │   ├── risk_reversal.md
    │   ├── fixed_strike_matrix.md
    │   ├── term_structure.md
    │   └── volatility_skew.md
    ├── OIH/  (same 9 files)
    ├── ... (one folder per tier-1 ticker)
    └── ORCL/
```

That's roughly 20-30 markdown files + 8 CSV files per trading day. Compressed in git: ~2-5 MB/day → ~1 GB/year. Not free but fine.

---

## Tier-1 ticker universe (for per-ticker deep-dive captures)

Same as `ALERT_TICKERS` in `price_monitor.py`, plus high-flow names from FlowPatrol:

**Equity ETFs:** SPY, QQQ, IWM, HYG, LQD, XLK, XOP, XTL, XLI, OIH, KRE, AAAU, ALLW, IBIT, GLD, SLV, USO, BNO, UNG

**Single stocks:** MSFT, AAPL, AMZN, META, GOOGL, NFLX, TSLA, NVDA, ORCL, MSTR, AMD, MU, PLTR, SHOP, WBD, NOK, BYND

**Indices (Equity Hub variants):** SPX, NDX, RUT

**FX equity wrappers:** FXE, FXY, FXB, FXC, UUP

**Treasury proxies:** TLT, IEF, TBT, SHY

That's ~50 tier-1 tickers. Per-ticker deep-dive captures (9 sub-tab files each) = 450 files per pre-market sweep + 450 per post-close. Heavy but the data is irreplaceable.

If 450 files × 2 sweeps/day proves too aggressive on Claude API costs, narrow to ~20 tier-1 names initially and add as cost permits.

---

## Postgres tables (build alongside the scraper)

The corpus on disk is useful; structured tables enable joins, day-over-day deltas, alignment scoring queries.

- `spotgamma_master_levels` — daily/hourly per-ticker (Symbol, snapshot_at, current_price, previous_close, key_gamma_strike, key_delta_strike, hedge_wall, call_wall, put_wall, plus the ~14 Scanner dimensions). 5,000 rows per snapshot × N snapshots/day.
- `spotgamma_index_metrics` — per-index daily comprehensive table from Founder's Notes
- `spotgamma_flowpatrol_positions` — per-stock per-day position changes (delta/gamma/vega rows from FlowPatrol tables)
- `spotgamma_unusual_positions` — daily unusual options positions
- `spotgamma_tape_top` — daily top movers / volume / largest trades
- `spotgamma_founders_notes` — full text of AM and PM notes per day, plus extracted key_levels JSON

Postgres schema migration follows the file-based scraper proving out — capture first, structure later when access patterns are clear.

---

## Implementation pattern

Each scheduled task is a Claude session executing a focused prompt with Claude in Chrome MCP available. The prompt:

1. Lists connected browsers (must be ≥ 1)
2. Iterates the page list for that schedule
3. For each page: navigate, wait for load, screenshot OR read_page, extract structured data
4. For the master table: trigger Export button → wait for CSV download → move to corpus folder
5. For per-ticker deep-dives: iterate tier-1 list, navigate each tab, extract
6. Write all snapshot files
7. Report summary: pages_succeeded, pages_failed

Failure modes handled gracefully (skip ticker if no data, log if redirect, retry once). Tasks are independent — Tuesday's failure doesn't block Wednesday.

The first run of each scheduled task requires user to approve Claude in Chrome tool prompts (matches "Ask before acting" mode). After first approval, subsequent runs are silent. SPY de-risk test on 2026-05-07 confirmed the architecture works.

---

## Storage estimates (revised)

- Daily file count: ~450 per-ticker deep-dive markdowns × 2 sweeps + ~30 narrative markdowns + ~8 CSVs = ~930 files
- Average size: per-ticker markdown ~5 KB, CSV ~500 KB
- Per day: ~9 MB pre-compression, ~3 MB compressed in git
- Per year: ~750 MB compressed in git for 250 trading days

Bigger than my earlier estimate. Still manageable for git but worth keeping an eye on. If git history bloats, we can move CSVs to a separate "data-archive" repo or to cloud blob storage and keep just markdown in the main repo.

---

## Build sequence (Friday and onward)

1. **Verify Master Table Export.** First task: load `/equityhub?sym=SPY`, scroll to the master table, configure all columns on (via Columns button), trigger Export, see what file format comes back. If CSV, the scraper writes the CSV directly to disk. If something else, adapt.
2. **Pre-market scheduled task.** Start with this one. Most valuable single capture.
3. **Per-ticker deep-dive loop.** Add as second scheduled task or as part of pre-market.
4. **Hourly intraday master table capture.** Run after pre-market is stable.
5. **Post-close sweep.** Mirrors pre-market structure, captures EOD data.
6. **FlowPatrol capture.** Daily, post-close timing (it publishes at end of day).

Build Friday, refine Saturday, run for a week, evaluate cost vs value, adjust cadence and depth.
