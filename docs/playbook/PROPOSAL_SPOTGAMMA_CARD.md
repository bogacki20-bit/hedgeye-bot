# Proposal: SpotGamma on the rule card (draft 11/15/2026, NOT on the card until Kris rules)

Evidence: `tools/sg_scorecard.py` -> `data/reviews/sg_scorecard_2026-11-16.md` (index history 2018-2026 for SPX, SPY, NDX,
QQQ, RUT, IWM; Equity Hub 6/20-11/14; old tape 6/15-10/23; tilt 8/6-11/12). Data source for all three rules:
`sg_index_daily` (vol_trigger, zero_gamma, call_wall, put_wall, l1-l4, c1-c4), captured daily by the capture desk.

## P1. Gamma sets the step size (replaces the "Negative gamma + risk-off" line in Market picture)
Current card line: "- **Negative gamma + risk-off:** new positions (long and short) at half-size steps."

Proposed:
- **Gamma sets the step size (SpotGamma scorecard 11/15).** SPX's next-day move averaged 1.11% below SpotGamma's
  Volatility Trigger vs 0.61% above it, and 1.28% vs 0.39% between the most negative and most positive fifths of net
  gamma (2018-2026; the same on NDX, QQQ, RUT and IWM). It predicted the size of moves, never the direction.
  - **Negative gamma = SPX below SpotGamma's Volatility Trigger** (the session's levels), or Tier 1 Alpha's gamma setup
    negative. New positions and adds, long and short, use **half-size steps, whatever the reading**. Recycle trims and
    covers, and every stop, stay as they are.
  - **Positive gamma = SPX above the Volatility Trigger:** normal steps; Tier 1 Alpha's RANGE setup applies (buy
    support, trim resistance).
  - **Half doesn't stack:** half-size from vol (VIX 20-29), gamma or the throttle is still one half.
  - Line 2 of every check-in shows "Gamma: neg/pos (SPX x vs VT y)".

## P2. The put wall is not a floor (SPY range section, after the reload bullet)
- **The put wall is not a floor (scorecard 11/15).** SPX closed through its put wall within 5 sessions 37.6% of the
  time, vs 30.9% for an ordinary level the same distance away; every index broke its put wall more often than chance.
  - While SPX is in negative gamma and at or below SpotGamma's put wall, **the rp 0.20-or-lower reload waits**: reload
    index and high-beta longs only after SPX closes back above the put wall.
  - Red-day short covers and the bottom-of-range cover still apply.

## P3. SpotGamma levels on the backboard (Layer 3, Backboard levels bullet)
- Add: **SpotGamma's Volatility Trigger, zero gamma, call wall, put wall, Large Gamma 1-4 and Combo strikes join the
  backboard levels** (context, not a vote), next to Tier 1 Alpha's flip and strikes.
- **(Optional) Call wall = trim zone for index ETFs:** the call wall capped a little (SPX crossed it 5 points less often
  than chance). When SPX is within 1% under its call wall, index-ETF green-day trims use 50 bp (the SPX top-of-range
  size) even below rp 0.80.

## Not proposed
- **Call-wall distance as a single-name tiebreaker:** dropped. It scored -0.07 (passes the probation bar), but after
  removing 1-month momentum it is about zero (-0.017 all names, +0.009 inside Hedgeye inventory). The card's range and
  higher-high/higher-low checks already carry that momentum.
- **The tape:** contrarian and weak on the old sample (bullish-flow days -> next day -0.14%, bearish -> +0.17%,
  correlation -0.14, 91 days). Context only ("who's buying"), re-scored on the new full 15-minute reads at the mid-December
  keep/cancel review.
- **Tilt:** predicted move size again (SPX -0.22, QQQ -0.28, 70 days). It overlaps P1; keep it as a check, not a rule.

## For Open (mid-December SpotGamma keep/cancel review)
- Re-score the tape (15-minute reads from 11/16) and call-wall distance net of momentum.
- Check P1-P3 against the conviction log if adopted.
