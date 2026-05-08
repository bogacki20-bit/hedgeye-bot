# SpotGamma PM Note — Thursday, May 07, 2026 at 5:30 PM ET

Source: https://dashboard.spotgamma.com/foundersNotes/
Captured: 2026-05-07 ~21:42 UTC (17:42 ET) via scheduled task `spotgamma-postclose-sweep` (re-run after PM Note published).

## Tape (header at capture time)

| Index    | Level     | Change        |
|----------|-----------|---------------|
| ^SPX     | 7,328.05  | -34.40 (-0.47%) |
| ^NDX     | 28,525.68 | -68.82 (-0.24%) |
| ^VIX     | 17.08     | +0.02 (+0.12%) |

Note: PM Note narrative cites SPX intraday close at 7,337 (-0.38%) — header tape shown above is the late-tape value as displayed in the SpotGamma header at capture time.

## Key dates ahead

- **5/8: NFP** — Friday's jobs print

## SG Summary (running journal — newest at top)

**Update 5/6 (most recent active update; carried forward):** Markets seem to pricing in an Iran deal as oil is down 9%, but nothing concrete has been announced. Regardless, between the AI-earnings beats, and oil<100, it's clear that a general vol decline and continued stock support should remain in tact through to OPEX 5/15. The removal of Iranian tail risk will only aid to stock support (vanna).

**Update 5/1 (older — strikethrough on site):** Positioning remains in place to support markets, and invokes stable positioning despite raising oil prices. Given this, we continue to ride a core long stock position while the SPX is > Risk Pivot, which is now 7,180. All signals point strong to the market not caring about the geopolitical situation, but we want to maintain tail hedges until oil is back <100.

**Update 4/28 (older — strikethrough on site):** We move to sell AMD 1-2 month to expiration AMD call structures as a view that 1) the IV is at extremes and we think the rate-of-change of upside slows. Additionally this would add to a downside equity market play as oil breaks >100. Additionally, we have raised our Risk Pivot to 7,090, which means we would shift to a directionally short position if the SPX breaks < that level as a break of 7,090 implies a quick test of 7,000.

## Key SG levels for SPX (current)

- **Resistance:** 7,350
- **Pivot:** 7,180 (bearish below, bullish above) — updated 5/1/26
- **Support:** 7,300, 7,280, 7,250

## PM commentary highlights

The equity market pulled back on Thursday following one of the strongest rallies in the past month, with semiconductor names also cooling (SMH -1.76%).

- **SPX:** traded an ~85bps intraday range, closed 7,337 (-0.38%)
- **Vol complex muted:** VIX 17 (-1.9%), VVIX 94 (-0.1%)

### Flow detail — "Seek and Destroy" algo active intraday

Price responded to shifts in the 99th percentile 0DTE gamma strike. After SPX pushed up toward 7,380 around 11:00am ET, the 99th percentile gamma strike rolled lower to ~7,355, which coincided with a reversal in S&P 500 HIRO from the session highs.

Switching from the GEX lens to the Net OI lens, dealer positioning showed short puts concentrated around the 7,320 area, which aligned with the session low.

Into the close, the 99th percentile gamma level shifted again to ~7,340, aligning with end-of-day charm hedging pressure and acting as a "pinning" level. Under the charm heatmap, blue shows buying pressure and red shows selling pressure.

### HIRO recap

S&P 500 HIRO registered approximately **+$4B in net delta** on the day, driven primarily by ~$4.1B of put selling and ~$0.7B of call selling. These flows were largely concentrated in 0DTE options, suggesting tactical positioning rather than longer-term directional exposure.

In contrast, S&P equities saw roughly **-$2B in HIRO flow**, led by ~$1.5B of put buying and ~$0.5B of call selling.

### Vol regime — pre-NFP premium

Despite the modest market pullback, fixed strike implied volatility declined slightly except for tomorrow's expiration. As noted in the AM Note, there appears to be some volatility premium tied to the upcoming NFP release. If NFP data does not have any surprise, then the shorter dated <May OPEX options will start to lose a lot of IV. In that scenario, vol sellers might re-engage into next Friday's OPEX, potentially driving a vanna-induced rally.

### FlowPatrol highlight — DDOG +31%

Datadog (DDOG) rallied +31% on the day post-earnings. SpotGamma flagged the trade in yesterday's FlowPatrol: 2,368 lots DDOG June 18 150 call bought to open with $2.3M premium paid. These calls rallied from $10 to $40 in one day — ~300% gains.

### Notable single-name flow

- **MSFT:** Bullish activity observed with ~12k August 390 calls traded. MSFT closed at $421 (+1.65%), above the key gamma level of $420.

## Comprehensive level table (per instrument, capture time)

| Field | /ESM26 | SPX | SPY | NDX | QQQ | RUT | IWM |
|---|---|---|---|---|---|---|---|
| Reference Price | $7,390.95 | $7,365 | $733 | $28,599 | $695 | $2,886 | $286 |
| SG Gamma Index™ | 5.897 | 0.135 | | | | | |
| SG Implied 1-Day Move | 0.65% | 0.65% | | | | | |
| SG Implied 5-Day Move | 1.49% | | | | | | |
| SG Implied 1-Day Move High | $7,422.48 | $739.86 | | | | | |
| SG Implied 1-Day Move Low | $7,326.62 | $730.30 | | | | | |
| SG Volatility Trigger™ | $7,210.95 | $7,185 | $730 | $26,640 | $689 | $2,750 | $279 |
| Absolute Gamma Strike | $7,025.95 | $7,000 | $730 | $29,000 | $690 | $2,800 | $285 |
| **Call Wall** | $7,425.95 | $7,400 | $740 | $29,000 | $700 | $2,900 | $285 |
| **Put Wall** | $6,825.95 | $6,800 | $700 | $25,500 | $600 | $2,715 | $270 |
| Zero Gamma Level | $7,130.95 | $7,105 | $726 | $25,867 | $683 | $2,815 | $286 |

| Field | SPX | SPY | NDX | QQQ | RUT | IWM |
|---|---|---|---|---|---|---|
| Gamma Tilt | 1.765 | 1.139 | 2.348 | 1.248 | 1.481 | 0.970 |
| Gamma Notional (MM) | $1.672B | $513.124M | $22.888M | $406.211M | $30.921M | $35.481M |
| 25 Delta Risk Reversal | -0.045 | -0.028 | -0.050 | -0.033 | -0.039 | -0.025 |
| Call Volume | 1.750M | 1.749M | 15.532K | 1.168M | 18.458K | 319.597K |
| Put Volume | 1.325M | 2.377M | 15.902K | 1.439M | 37.874K | 837.921K |
| Call Open Interest | 9.63M | 5.852M | 85.137K | 4.317M | 228.621K | 2.963M |
| Put Open Interest | 13.33M | 13.735M | 91.423K | 6.938M | 429.776K | 8.018M |

## Key Support & Resistance Strikes

- **SPX:** [7000, 7350, 7400, 7300]
- **SPY:** [730, 733, 735, 700]
- **NDX:** [29000, 27000, 28600, 28500]
- **QQQ:** [690, 700, 695, 675]

## SPX Combos (top, with confidence %)

7726 (69.12%), 7697 (93.48%), 7674 (78.06%), 7652 (85.75%), 7623 (73.15%), **7601 (97.85%)**, 7579 (70.64%), 7571 (88.34%), 7549 (90.12%), 7527 (94.29%), 7512 (69.86%), **7498 (99.60%)**, 7476 (95.43%), 7468 (67.45%), 7461 (79.49%), **7454 (99.22%)**, 7446 (71.38%), 7439 (83.85%), 7431 (96.91%), **7424 (99.06%)**, 7417 (97.36%), 7409 (94.26%), **7402 (99.92%)**, 7395 (89.38%), 7387 (98.16%), 7380 (98.02%), **7372 (99.66%)**, 7365 (86.49%), 7358 (92.99%), **7350 (99.48%)**, 7328 (95.31%), 7321 (82.64%), 7314 (91.05%), 7306 (70.04%), 7299 (97.70%), 7269 (78.70%), 7247 (84.44%), 7203 (92.24%), 7174 (66.10%), 7026 (74.72%)

## SPY / NDX / QQQ Combos

- **SPY Combos:** 728.08, 733.15, 737.49, 730.25
- **NDX Combos:** 29000, 28771, 28599, 29200
- **QQQ Combos:** 684.92, 689.69, 693.10, 649.48

## Cross-reference notes for the bot

- Call Wall **stayed at SPX 7,400** post-close (migrated up from 7,300 yesterday). Resistance level at 7,350 is now **above** spot — the recent break-and-hold above 7,300/7,350 has flipped these to support layers in the SG Combos table.
- Pivot held at **7,180** (bullish above, bearish below).
- Vol complex still pre-NFP: VIX 17 / VVIX 94 — Sebastian's framework: a "no-surprise" NFP could trigger a vanna-induced rally into 5/15 OPEX. Hedgeye signals into XOP/OIH should be sized cautiously into that vol-unwind setup.
- DDOG +31% post-earnings is a SpotGamma FlowPatrol-flagged win — useful precedent if Hedgeye flags AI/cloud names with similar call flow signature.
- MSFT closed $421 (+1.65%) above $420 key gamma — relevant for the EOD MSFT snapshot in `equityhub_eod/MSFT.md` (which captured $420 at ~17:21 ET).
