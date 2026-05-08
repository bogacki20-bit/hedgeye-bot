# SpotGamma SPY Equity Hub — 2026-05-07 (scheduled de-risk test)

Source: https://dashboard.spotgamma.com/equityhub?sym=SPY&date=2026-05-07&eh-model=legacy
Captured by scheduled task `spotgamma-scrape-test` via Claude in Chrome.

## Headline data

- **Current price:** $733.75
- **Daily change:** +1.38% (green day)
- **Previous close:** $723.74
- **Earnings date:** —

## Key SpotGamma levels

| Level                | Price    | Notes                                          |
|----------------------|----------|------------------------------------------------|
| Call Wall            | $730     | Resistance — dealers most short gamma here     |
| Key Gamma Strike     | $724     | Near current price / last close                |
| Last Closing         | $723.74  | Yesterday's close                              |
| Hedge Wall           | $722     | Active dealer hedge zone                       |
| Key Delta Strike     | $700     | Coincides with Put Wall                        |
| Put Wall             | $700     | Downside support, ~4.6% below current          |

## Skew & vol

- **Skew Rank:** 65.60% — moderate put pricing premium vs calls
- **IV Rank:** 22.98% — implied vol toward lower end of historical range

## Gamma / delta exposure

- **Call Gamma:** -3.14B
- **Put Gamma:** -2.86B
- **Gamma Hedge Est:** -8,280,040
- **Top Gamma Expiry:** 2026-05-06 (Largest Gamma Strike date)
- **Top Delta Expiry:** 2026-05-15 (Largest Delta Strike date)

## Macro tape (capture time)

- ^SPX: 7,370.05 (+0.10%)
- ^NDX: 28,617.30 (+0.08%)
- ^VIX: 17.50 (+1.39%)

## Framework cross-reference

Price has pushed *through* the $730 Call Wall to $733.75 — dealers who were short gamma at $730 are now actively hedging into strength, which can amplify the move (gamma squeeze geometry). Key Gamma Strike $724, Last Close $723.74, and Hedge Wall $722 stack tightly just below — that band is the "gravity well" if a pullback materializes. Put Wall + Key Delta Strike both at $700 = clean ~4.6% downside cushion.

Skew Rank 65.6% is moderate (not extreme), and IV Rank 22.98% is low — fear premium is muted, options are relatively cheap. With price above the Call Wall and gamma exposure net negative ($-8.28M hedge est), the tape is in a regime where dealer flow is procyclical to the upside until/unless price falls back through the $722–$724 hedge band.

## De-risk test status

This file was written by an automated scheduled task (`spotgamma-scrape-test`) under the Hedgeye Bot Cowork install — verifying that scheduled runs can drive Claude in Chrome end-to-end.

- **Run fired (UTC):** 2026-05-07 09:15:37
- **Today's date (per env):** 2026-05-07
- **Browser connection:** confirmed — `Browser 1` (Windows, isLocal=true, deviceId 90ec952a-0dec-45a6-916e-374502057c6a)
- **Tools available & exercised:**
  - `mcp__Claude_in_Chrome__list_connected_browsers` — listed 1 browser
  - `mcp__Claude_in_Chrome__select_browser` — selected Browser 1
  - `mcp__Claude_in_Chrome__tabs_context_mcp` (createIfEmpty=true) — got tab 463555990
  - `mcp__Claude_in_Chrome__navigate` — to dashboard.spotgamma.com/equityhub?sym=SPY (auto-redirected to include `&date=2026-05-07&eh-model=legacy`)
  - `mcp__Claude_in_Chrome__computer` — wait, screenshot, left_click ("Show more")
  - `mcp__Claude_in_Chrome__browser_batch` — used to chain navigate/wait/screenshot in one round trip
  - `Write` (file tool) — wrote this snapshot
  - `mcp__workspace__bash` — created the `2026-05-07/` directory
- **Page state:** SPY Equity Hub rendered fully, right-panel data cells populated, "Show more" panel expanded successfully to reveal Call/Put Gamma + Top Gamma/Delta Expiry dates.
- **No login wall hit:** session was already authenticated in the browser profile — the scheduled run inherited an active SpotGamma session, which is the desired behavior for the production scraper.
- **Outcome:** PASS. Scheduled tasks in this Cowork install can drive Claude in Chrome to navigate SpotGamma, extract the Equity Hub data cells, and write a structured snapshot file. Architecture is de-risked for the SpotGamma scraper build.
