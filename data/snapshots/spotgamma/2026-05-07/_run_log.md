# SpotGamma Pre-Market Capture — Run Log 2026-05-07

- **Run start:** 2026-05-07 ~12:40 UTC (08:40 ET) — current scheduled run
- **Run end:**   2026-05-07 ~12:49 UTC (08:49 ET)
- **Total elapsed:** ~9 minutes
- **Browser:** Browser 1 (Windows, deviceId 90ec952a-…)
- **Trigger:** Scheduled task `spotgamma-premarket-sweep`

> Note: Two prior runs executed earlier today (~10:55 UTC verification run and ~11:08 UTC scheduled run) wrote initial files. This run refreshed the macro tape on all 15 equity hub captures with current pre-market values and — critically — captured today's AM Founder's Note, which had not yet published in the prior runs. Per-ticker SpotGamma levels (Call Wall, Put Wall, Key Gamma/Delta, etc.) and ticker prices were essentially unchanged across the runs since markets remain in pre-market.

## Capture 1 — Per-ticker Equity Hub (SUCCESS, 15/15)

All 15 tier-1 tickers captured to `data/snapshots/spotgamma/2026-05-07/equityhub/` with refreshed macro tape headers:

| Ticker | File              | Status | Last $    |
|--------|-------------------|--------|-----------|
| SPY    | equityhub/SPY.md  | OK     | $733.75   |
| QQQ    | equityhub/QQQ.md  | OK     | $695.45   |
| IWM    | equityhub/IWM.md  | OK     | $286.37   |
| HYG    | equityhub/HYG.md  | OK     | $80.17    |
| LQD    | equityhub/LQD.md  | OK     | $109.18   |
| XLK    | equityhub/XLK.md  | OK     | $169.59   |
| XOP    | equityhub/XOP.md  | OK     | $169.39   |
| XLI    | equityhub/XLI.md  | OK     | $177.05   |
| OIH    | equityhub/OIH.md  | OK     | $432.87   |
| KRE    | equityhub/KRE.md  | OK     | $70.60    |
| MSFT   | equityhub/MSFT.md | OK     | $413.50   |
| AAPL   | equityhub/AAPL.md | OK     | $287.48   |
| NVDA   | equityhub/NVDA.md | OK     | $207.14   |
| TSLA   | equityhub/TSLA.md | OK (Key Delta Strike scraped value "$5" remains anomalous from upstream — flagged in file from earlier run) |
| IBIT   | equityhub/IBIT.md | OK     | $46.12    |

Macro tape at capture time: ^SPX 7,377 (+0.20%), ^NDX 28,640 (+0.16%), ^VIX 17.35 (+0.52%).

## Capture 2 — AM Founder's Note (SUCCESS — newly published)

The Thursday 5/7 AM Founder's Note published at 7:00 AM ET and was captured this run.

- File: `data/snapshots/spotgamma/2026-05-07/founders_note_am.md`
- Source: dashboard.spotgamma.com/foundersNotes (most recent entry)
- Stub from prior runs (`founders_note_am_NOT_PUBLISHED_YET.md`) is now stale; could not be removed (filesystem permission), but `founders_note_am.md` supersedes it.

Highlights captured:
- Macro Theme: futures +10bps, no major news on tap
- Single-name: ARM -8% post-earnings
- 0DTE cluster forming at 7,420 (vs yesterday's perfect pin at 7,365)
- "Stock up + vol up" phenomenon — June skew +2 vol points higher across the board
- View: NFP passing tomorrow → IV unwind → SPX may begin pinning into 5/15 OPEX
- Key SG levels: Resistance 7,350 / Pivot 7,180 / Support 7,300, 7,280, 7,250
- Comprehensive SPX/SPY/NDX/QQQ/RUT/IWM level tables with Call Wall (SPX) at 7,400 (migrated up from 7,300)

## Capture 3 — Market Overview (SUCCESS)

- File: `data/snapshots/spotgamma/2026-05-07/market_overview.md`
- Source: dashboard.spotgamma.com/home?eh-model=legacy
- Screenshot taken (chart annotations: Call Wall 7,425.95 / Last Closing 7,391.07)
- Macro tape refreshed: ^SPX 7,375.85 (+0.18%), ^NDX 28,633.20 (+0.14%), ^VIX 17.36 (+0.58%)
- Index Levels (SPX/ES) full table preserved
- Index Metrics, Support & Resistance Strikes, SPX Combos retained from prior capture (data static between runs)
- Events Calendar: NFP/Unemployment/Michigan tomorrow 5/8; CPI cluster Tue 5/12

## Failures

None. All three captures succeeded.

## Notes for downstream consumers

- `founders_note_am_NOT_PUBLISHED_YET.md` from prior run is now stale — read `founders_note_am.md` instead. If the bot's stale-file cleanup needs to handle this, it should treat any `_NOT_PUBLISHED_YET.md` as superseded once the corresponding non-stub file exists.
- All equity hub files have a fresh macro tape header showing the current ^SPX / ^NDX / ^VIX read at capture time.
- TSLA Key Delta Strike scraped value "$5" persists upstream — the Equity Hub right panel shows "Hedge Wall $390" but the daily-levels table row for TSLA shows "Key Delta Strike: 5" which is anomalous; the chart's Key Delta Strike label is missing in this snapshot. Flagged in TSLA.md from earlier run.

---

# SpotGamma Post-Close Capture — Run Log 2026-05-07

- **Run start:** 2026-05-07 ~21:21 UTC (17:21 ET)
- **Run end:**   2026-05-07 ~21:30 UTC (17:30 ET)
- **Total elapsed:** ~9 minutes
- **Browser:** Browser 1 (Windows, deviceId 90ec952a-…)
- **Trigger:** Scheduled task `spotgamma-postclose-sweep`

Macro tape at capture time (post-close):
- ^SPX 7,328.05 (-34.40, -0.47%)
- ^NDX 28,525.68 (-68.82, -0.24%)
- ^VIX 17.08 (+0.02, +0.12%)
- WTI $98.47 (+2.31, +2.40%)
- Gold $4,685.77 (-7.85, -0.17%)

## Capture 1 — Today's PM Founder's Note (NOT_PUBLISHED_YET)

The Thursday 5/7 PM Founder's Note had **not yet published** at run time (5:21 PM ET). Most recent PM Note in the sidebar was Wed 5/6. Stub file written:

- File: `data/snapshots/spotgamma/2026-05-07/founders_note_pm_NOT_PUBLISHED_YET.md`

PM Notes typically publish 5:00-5:30 PM ET — this run was a few minutes early. Re-run after 5:30 PM ET to capture today's PM Note.

## Capture 2 — Today's FlowPatrol Report (SUCCESS)

The Thursday 5/7 FlowPatrol report had published. Full 19-page report extracted.

- File: `data/snapshots/spotgamma/2026-05-07/flowpatrol.md`
- Headline: **"SPX Sees Extreme Bullishness Amid Broader Mixed Flows"**
- Key insights captured: SPX 100th-%ile delta ($29.7B) + 100th-%ile gamma ($3.1B) + 100th-%ile vega ($263M); $615M premium on SPX 8070C 12/31; AMD/TSLA/NVDA single-stock bearish flow; KWEB extreme gamma; BTDR/DKNG unusual call buying.

## Capture 3 — Per-ticker Equity Hub EOD (SUCCESS, 15/15)

All 15 tier-1 tickers captured to `data/snapshots/spotgamma/2026-05-07/equityhub_eod/` with EOD price + framework comparisons to morning capture:

| Ticker | File                  | EOD $     | AM $      | Daily % |
|--------|-----------------------|-----------|-----------|---------|
| SPY    | equityhub_eod/SPY.md  | $730.41   | $733.75   | -0.46%  |
| QQQ    | equityhub_eod/QQQ.md  | $694.07   | $695.45   | -0.22%  |
| IWM    | equityhub_eod/IWM.md  | $281.95   | $286.37   | -1.70%  |
| HYG    | equityhub_eod/HYG.md  | $79.83    | $80.17    | -0.42%  |
| LQD    | equityhub_eod/LQD.md  | $108.75   | $109.18   | -0.38%  |
| XLK    | equityhub_eod/XLK.md  | $169.51   | $169.59   | -0.26%  |
| XOP    | equityhub_eod/XOP.md  | $165.92   | $169.39   | -2.02%  |
| XLI    | equityhub_eod/XLI.md  | $173.95   | $177.05   | -1.65%  |
| OIH    | equityhub_eod/OIH.md  | $418.00   | $432.87   | -3.43%  |
| KRE    | equityhub_eod/KRE.md  | $70.00    | $70.60    | -0.96%  |
| MSFT   | equityhub_eod/MSFT.md | $420.00   | $413.50   | +1.51%  |
| AAPL   | equityhub_eod/AAPL.md | $287.00   | $287.48   | -0.18%  |
| NVDA   | equityhub_eod/NVDA.md | $211.88   | $207.14   | +2.03%  |
| TSLA   | equityhub_eod/TSLA.md | $410.31   | $398.00   | +2.96%  |
| IBIT   | equityhub_eod/IBIT.md | $45.24    | $46.12    | -2.01%  |

Notable intraday moves: TSLA +2.96%, NVDA +2.03%, MSFT +1.51% rallied while OIH -3.43%, XOP -2.02%, IBIT -2.01%, IWM -1.70%, XLI -1.65% sold off. Rotational tape — mega-cap tech up, cyclicals/commodities/small-caps down.

Note: For XLK the right-panel data initially didn't render in extracted text; collapsing the scanner panel via the "Collapse" button restored the panel and full data was captured. No structural issues with the rest of the captures.

## Capture 4 — Market Overview EOD (SUCCESS)

- File: `data/snapshots/spotgamma/2026-05-07/market_overview_eod.md`
- Screenshot captured for chart annotations
- **Key structural change vs 5/6 PM:** SPX Call Wall **migrated up from 7,300 to 7,400** following yesterday's break. Volatility Trigger 7,185, Zero Gamma 7,105, Put Wall 6,800 — long-dated structure unchanged.
- Squeeze Candidates table preserved (top 30 by call gamma)
- Events Calendar: NFP + Unemployment + Michigan Sentiment all tomorrow (5/8); CPI cluster Tue 5/12

## Failures

- **Capture 1 (PM Founder's Note)**: not yet published at run time. Stub file written. Recommend re-running scheduled task after 5:30 PM ET, or scheduling the post-close sweep at 5:30 PM ET going forward.

## Notes for downstream consumers

- The PM Note stub file `founders_note_pm_NOT_PUBLISHED_YET.md` should be cleaned up if/when a manual re-run captures today's PM Note. The morning sweep's similar stub for the AM Note is already superseded by the published version.
- New `equityhub_eod/` subfolder created — this matches the task spec (separates EOD from morning `equityhub/` capture).
- `market_overview_eod.md` filename distinguishes from the morning's `market_overview.md` (note: 5/7 AM run did NOT produce a `market_overview.md` — the morning sweep captured market overview as a separate run; cross-day comparison done against 5/6 PM in the EOD file).
- All EOD ticker files include a "Framework read" comparing intraday change to the morning AM capture.

---

# SpotGamma Post-Close Capture — Re-Run 2026-05-07 (PM Note recovery)

- **Run start:** 2026-05-07 ~21:41 UTC (17:41 ET)
- **Run end:**   2026-05-07 ~21:45 UTC (17:45 ET)
- **Total elapsed:** ~4 minutes
- **Browser:** Browser 1 (Windows, deviceId 90ec952a-…)
- **Trigger:** Scheduled task `spotgamma-postclose-sweep` (second invocation today)
- **Purpose:** Recover the PM Founder's Note that had not yet published during the 17:21 ET sweep.

Macro tape at re-run capture time (post-close, unchanged from prior run):
- ^SPX 7,328.05 (-34.40, -0.47%)
- ^NDX 28,525.68 (-68.82, -0.24%)
- ^VIX 17.08 (+0.02, +0.12%)

## Capture 1 — Today's PM Founder's Note (SUCCESS — newly recovered)

The Thursday 5/7 PM Founder's Note **published at 5:30 PM ET** (timestamp on report header: "PM Note: Thu, May 07, 2026 at 5:30 PM ET"). Captured this run.

- File: `data/snapshots/spotgamma/2026-05-07/founders_note_pm.md`
- Source: dashboard.spotgamma.com/foundersNotes (most recent entry, marked NEW)
- Stub from prior run (`founders_note_pm_NOT_PUBLISHED_YET.md`) is now stale — `founders_note_pm.md` supersedes it. Stub left in place (filesystem permission); downstream consumers should treat any `_NOT_PUBLISHED_YET.md` as superseded once the corresponding non-stub file exists.

Highlights captured:
- SPX intraday close 7,337 (-0.38%); ~85bps intraday range
- VIX 17 (-1.9%), VVIX 94 (-0.1%) — vol complex muted
- "Seek and Destroy" algo active: SPX rejected at ~7,380 mid-morning when 99th-pct 0DTE gamma rolled to 7,355; into-close gamma shifted to 7,340 with charm pinning pressure
- HIRO recap: S&P 500 HIRO **+$4B net delta** (mostly 0DTE put selling and call selling); S&P equities -$2B HIRO
- Pre-NFP IV premium intact for tomorrow's expiry; "no-surprise NFP → vanna-induced rally into 5/15 OPEX" thesis
- DDOG +31% post-earnings — SpotGamma flagged the 6/18 150C trade in yesterday's FlowPatrol (~300% gain on the calls)
- MSFT closed $421 (+1.65%) above $420 key gamma — bullish ~12k Aug 390 calls activity
- Comprehensive level table: Call Wall SPX **7,400** (held), Put Wall 6,800, Volatility Trigger 7,185, Zero Gamma 7,105 (structure unchanged from morning, consistent with Market Overview EOD capture)

## Capture 2 — FlowPatrol (verified, no re-capture needed)

FlowPatrol report on the dashboard is the same 2026-05-07 entry already captured in the 17:21 ET sweep ("SPX Sees Extreme Bullishness Amid Broader Mixed Flows", 19-page report, 347 lines in saved file). No update detected. Existing `flowpatrol.md` retained.

## Capture 3 — Per-ticker Equity Hub EOD (skipped, no re-capture needed)

Post-close per-ticker level data (Call Wall, Put Wall, Key Gamma, Key Delta, Skew/IV Rank, etc.) was captured at 17:21 ET, ~21 minutes after close. SpotGamma's EOD model is computed once at close — re-pulling 20 minutes later returns the same values. All 15 files in `equityhub_eod/` retained as-is.

## Capture 4 — Market Overview EOD (verified, no re-capture needed)

Market Overview page checked: macro tape, Index Levels table (Call Wall 7400/7426, Volatility Trigger 7185/7211, Zero Gamma 7105/7131, Put Wall 6800/6826), Squeeze Candidates, and Events Calendar all match the prior 17:21 ET capture exactly. Existing `market_overview_eod.md` retained.

## Failures

None.

## Net result for downstream consumers

All four captures for 2026-05-07 are now complete. The corpus folder `data/snapshots/spotgamma/2026-05-07/` contains:

- `founders_note_am.md` — today's AM Note
- `founders_note_pm.md` — today's PM Note (new in this re-run)
- `flowpatrol.md` — today's FlowPatrol
- `market_overview.md` — morning Market Overview
- `market_overview_eod.md` — post-close Market Overview
- `equityhub/` — 15 morning per-ticker captures
- `equityhub_eod/` — 15 post-close per-ticker captures
- Two stub `_NOT_PUBLISHED_YET.md` files left in place (now superseded)

Recommendation: schedule `spotgamma-postclose-sweep` at **5:35 PM ET** going forward to capture the PM Note in a single run (it consistently publishes 5:00-5:30 PM ET).
