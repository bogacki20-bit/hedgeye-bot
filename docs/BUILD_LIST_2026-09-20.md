# Desk-audit build list — status (2026-09-20)

Source: the desk-LLM's review of the 9/14–9/18 packets. Original list in
the session transcript; this file tracks what shipped and what's open.

## Shipped 9/20 (commits 76949cf bot / 7ba4738 canary)

- **#1 Quad** — set monthly Quad 2 / quarterly Quad 4 (Hedgeye changed it
  mid-September; bot sat on Quad 1 from 9/11). Added >7d confirm-age
  warning to REPORT and EOD headers (calendar staleness can't see
  mid-month changes).
- **#2 Hedge-wall gate** — `tools/walls_table.gate_walls()`: single-name
  hedge wall >35% from spot → gated (Vol Trigger is dense-chain-only).
  Wired into walls tables, SCREEN/REPORT sg suffix, MARKET.
- **#9 Inverted maps** — cw<pw (LFST 10/12) → whole map excluded, printed
  as ⋄broken-map. Same helper.
- **#3 Regime buffer** — three-state tilt: >1.10 PINNED/FADE · <0.90
  FOLLOW · 0.90–1.10 MIXED (size between). Desk note, MARKET header,
  zone-entry hint.
- **#5 Range gate** — `rp_resolve.resolve_rp`: fresh Hedgeye band
  (derived-hdg) now outranks MFR-published. v_screener was already
  correct; the resolver threw hdg away. 19/19 tests.
- **#11 FRED** — key exists on Railway as FRED_API_KEY; local packs were
  the ones missing it → copied into canary .env.
- **#12 BUXX scope** — pace/ledger/note count Individual (X96383748)
  only; Roth BUXX can't be margined.
- **#4 Stale book** — process, not code: Monday upload includes
  Accounts_History.csv (report already flags staleness loudly).

## Open — P1

- **#6 Blended P/L vs the short clock** — adds walk avg cost up on a
  working short (JETS read +1.0% over "53 sessions" but was scaled 9/16,
  9/17). Fix: per-lot P/L or suppress stale flag when adds exist in
  trailing N sessions. Also: JETS = energy hedge (XLE corr −0.71) —
  needs a "hedge leg, exempt from clock, dies with its parent" tag.
- **#7 Spread modeling** — verticals as one row: net debit / max gain /
  breakeven / DTE / **% of max** (the 70–80% take-off rule keys on it).
  IWM 285/280 9/30 ($126 debit), SMH 545/540 10/16 ($159 debit).
- **#8 Sector caps disagree** — framework 20% cluster warn vs book_rp
  8/12 warn/reject; energy 21.4% currently [REJECT]. **Kris must pick
  the numbers** — proposal: warn 15% / reject 25%, energy exempt to 25
  (deliberate overweight).
- **#10 Stale-price detector** — doctor check: identical to-the-cent
  price 3+ sessions (FXH 129.36, CACC 582.31) = dead feed; also absurd
  wall jumps (WEAT pw 1→37).

## Open — P2

- **#13 Link harvest** — built 9/20 (hedgeye_links.py, Cloudflare-polite).
  Extend: feed_item ids from plain text bodies, cloudfront chart PNGs,
  backfill once clearance behaves.
- **#14 Tilt for all six** — store SPX/SPY/NDX/QQQ/RUT/IWM (SPX vs SPY
  disagreed 0.36 all week; the regime choice should be testable).
  indices_capture currently intercepts only the tabs it opens.
- **#15 Zero Gamma / Abs Gamma / Combo strikes** — in the raw feed, not
  in schema.
- **#16 Use dpi / p/c / expected move** — stored, unread. Expected move
  is now the spread-width input per the framework.

## Open — P3 (scorecard/backtest — Saturday walls_scorecard candidates)

- **#17 SS count time series** (52→35 in a week; 80v35 shorts/longs).
- **#18 Trend-change churn gate** (COPPER 4 flips in 4 sessions).
- **#19 Alert holding-period capture** — Hedgeye shorts all ≤2 sessions;
  test book's short edge at 1/2/3/5-day horizons.
- **#20 Cross-source agreement flag** (ETF Pro + SG same name same day).
- **#21 Wall stability as confidence weight** (5-session unmoved = firm).
- **#22 Re-grade regime cells with MIXED excluded** — highest-value
  scorecard check; 3 of 5 sessions last week were inside the band.
- **#23 Discretionary-call grading** (the 9/14 financials exit).

## Data gaps

- Position Monitors download (feed_item 187142) — 15 sector monitors.
- EWL, SPXC dark — MFR enrollment.
