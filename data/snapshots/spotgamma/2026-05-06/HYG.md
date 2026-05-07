# SpotGamma HYG Equity Hub — 2026-05-06 evening

Source: https://dashboard.spotgamma.com/equityhub?sym=HYG&date=2026-05-06&eh-model=legacy
Captured during walkthrough with Kristian.

## Headline data

- **Current price:** $80.17
- **Daily change:** +0.31% (green day)
- **Previous close:** $79.92

## Key SpotGamma levels

| Level                     | Price    | Notes                                                   |
|---------------------------|----------|---------------------------------------------------------|
| Call Wall                 | $81      | Resistance                                              |
| Key Gamma Strike          | $80      | Coincides with Hedge Wall — at current price            |
| Hedge Wall                | $80      | Coincides with Key Gamma Strike                         |
| Key Delta Strike          | $80.5    | Active dealer hedge zone, just above price              |
| Last Closing              | $79.92   | Yesterday's close, mid-range                            |
| Put Wall                  | $79      | Downside support                                        |

## Gamma posture

- **Gamma Hedge Est:** -108,909,437 (NEGATIVE — dealers net short gamma)
- Short-gamma regime ⇒ breakouts past $81 or breaks below $79 get AMPLIFIED, not damped.
- Largest Gamma Strike: 05/15/2026 (next monthly expiry)

## Macro tape (capture time)

- ^SPX: 7,362.55 (~+0.10%)
- ^NDX: 28,561.90 (-0.11%)
- ^VIX: 17.39 (+0.75%)

## Hedgeye context

- **2026-05-05 Risk Range:** HYG BULLISH, buy_trade $79.67, sell_trade $80.22, prev_close $79.80
- **2026-05-06 Portfolio Solutions:** Keith TRIMMED 50bps HYG
- Today's $80.17 sits ONE CENT below Hedgeye's $80.22 sell_trade level — literally at top of Keith's Risk Range

## Framework cross-reference

The geometry: HYG sits in a tight structural corridor. Put Wall ($79) to Call Wall ($81) is only $2 wide. Hedge Wall and Key Gamma Strike both at $80 (right at current price). High-yield bonds are inherently low-vol; the gamma map confirms that.

Today HYG is at the TOP of its $79-$81 corridor:
- One cent below Hedgeye sell_trade
- About 50 cents below the active hedge zone (Key Delta Strike $80.5)
- $0.83 from the Call Wall resistance ceiling

Keith's trim at $80.17 is structurally aligned:
- Hedgeye framework: "top of range, trim 1%+ position" ✓
- SpotGamma framework: "approaching Call Wall, dealers short gamma overhead, $81 is sticky resistance unless breakout" ✓
- Both frameworks AGREE: this is the trim zone

## Meta-pattern observation (today's walkthrough)

| Trade            | Hedgeye view              | SpotGamma view              | Agreement | Conviction |
|------------------|---------------------------|-----------------------------|-----------|------------|
| OIH (50bps add)  | Buy weakness, Quad 2 sector | Clean entry geometry, fear premium | YES | HIGH |
| HYG (50bps trim) | Top of range, trim         | Approaching Call Wall, sticky ceiling | YES | HIGH |
| SPX (above range)| Trim, top of range         | Above Call Wall, squeeze continuation | NO  | REGIME CALL |
| AAPL (above range)| Trim, top of range        | Positive gamma + bullish HIRO flow | NO  | REGIME CALL |

**The signal worth modeling:** when both frameworks agree on direction, conviction is high. When they disagree, it's a regime question requiring weighting by Quad and time horizon. Today's Keith trades (OIH add, HYG trim) are both clean two-framework agreements — those are the cleanest training rows for the eventual `alerts_log` + `actions_log` + `outcomes_log` ML corpus.
