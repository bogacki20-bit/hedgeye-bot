# SpotGamma PM Note — Wednesday, May 06, 2026 at 5:05 PM ET

Source: https://dashboard.spotgamma.com/foundersNotes/
Captured during walkthrough with Kristian.

## Tape (header at capture time)

| Index    | Level     | Change       |
|----------|-----------|--------------|
| ^SPX     | 7,370.05  | +7.60 (+0.10%) |
| ^NDX     | 28,614.82 | +20.32 (+0.07%) |
| ^VIX     | 17.49     | +0.23 (+1.33%) |
| WTI      | $93.79    | -$2.37 (-2.46%) |
| Gold     | $4,735.85 | +$42.23 (+0.90%) |

WTI down hard today (-2.46%) on Iran-deal pricing. Relevant to OIH thesis from earlier walkthrough — if oil keeps falling, OIH thesis weakens.

## Key dates ahead

- **5/8: NFP** — Friday's jobs print

## SG Summary (running journal — newest at top)

**Update 5/6 (today):** Markets seem to pricing in an Iran deal as oil is down 9%, but nothing concrete has been announced. Regardless, between the AI-earnings beats, and oil<100, it's clear that a general vol decline and continued stock support should remain intact through to OPEX 5/15. The removal of Iranian tail risk will only aid stock support (vanna).

**Update 5/1 (older — strikethrough on site):** Positioning remains in place to support markets, and invokes stable positioning despite rising oil prices. Continue to ride a core long stock position while the SPX is > Risk Pivot, which is now 7,180. All signals point strong to the market not caring about the geopolitical situation, but maintain tail hedges until oil is back <100.

**Update 4/28 (older — strikethrough):** Move to sell AMD 1-2 month to expiration AMD call structures as a view that 1) the IV is at extremes and we think the rate-of-change of upside slows. Additionally this would add to a downside equity market play as oil breaks >100. Risk Pivot raised to 7,090, which means shift to a directionally short position if the SPX breaks < that level (a break of 7,090 implies a quick test of 7,000).

## Key SG levels for SPX (current)

- **Resistance:** 7,350
- **Pivot:** 7,180 (bearish below, bullish above) — updated 5/1/26
- **Support:** 7,300, 7,280, 7,250

## PM commentary highlights

The stock market pushed to another all-time high following headlines that the U.S. and Iran are nearing a deal. Bonds also rose, while crude oil declined sharply, falling below $100.

- **SPX:** closed 7,365 (+1.46%), above Call Wall at 7,300
- **NDX:** +2.09% on the day, robust action in semis (SMH +5.18%)
- **Vol complex mixed:** VIX 17 (+0.17%), VVIX 94 (-1.64%)

### Flow detail — "seek and destroy" algo

After a 99th percentile 0DTE gamma concentration formed near 7,360 around 10:30am ET, S&P 500 HIRO reversed from declining to rising. By the close, S&P 500 HIRO moved from -$2B to +$10B, implying roughly $12B of delta for dealers to buy.

Net OI lens: ~35k lot 0DTE short call position at 7,360 around 10:30am ET. As price approached, a portion appeared to close near 11:30am ET. Simultaneously, TRACE gamma heatmap flipped from positive (purple) to negative (red), signaling shift toward more unstable price conditions. By end of day, the 7,360 level was breached.

S&P 500 HIRO finished +$10B delta — ~$7B in call buying and ~$3B in put selling. Nearly all the put selling occurred in 0DTE; majority of call buying was in longer-dated expirations (risk-on sentiment + demand for upside exposure).

S&P equities generated $4.8B in HIRO flows, exceeding prior 30-day highs. ~$4.3B from Mag 7 — continued demand for AI-driven equities.

Implied vol increased despite the rally — SPX implied vols for expirations through May 8 rose ~3-3.5 vol points (positioning long vol into NFP).

### Notable single-name flows

- **NVDA:** ~26k contracts of NVDA May and July $195 calls trading actively above the ask. July $195 call volume exceeded open interest, suggesting new positioning. NVDA closed $208 (+5.77%), above Key Gamma Strike, Key Delta Strike, and Call Wall at $200.
- **TSLA:** ~11k contracts of TSLA July $390 calls traded above ask, size exceeded open interest — likely new positioning. TSLA closed $399 (+2.40%), just below Key Gamma Strike and Call Wall at $400.

## Comprehensive level table (per instrument, capture time)

| Field | /ESM26 | SPX | SPY | NDX | QQQ | RUT | IWM |
|---|---|---|---|---|---|---|---|
| Reference Price | $7284.6 | $7259 | $723 | $28015 | $681 | $2845 | $282 |
| SG Gamma Index™ | 4.59 | 0.100 | | | | | |
| SG Implied 1-Day Move | 0.65% | 0.65% | | | | | |
| SG Implied 5-Day Move | 1.49% | | | | | | |
| SG Implied 1-Day Move High | $7353.54 | $732.92 | | | | | |
| SG Implied 1-Day Move Low | $7258.56 | $723.46 | | | | | |
| SG Volatility Trigger™ | $7220.6 | $7195 | $722 | $26640 | $674 | $2720 | $279 |
| Absolute Gamma Strike | $7025.6 | $7000 | $724 | $28000 | $680 | $2800 | $270 |
| **Call Wall** | $7325.6 | $7300 | $730 | $26700 | $685 | $2785 | $285 |
| **Put Wall** | $6825.6 | $6800 | $700 | $25500 | $600 | $2715 | $270 |
| Zero Gamma Level | $7134.6 | $7109 | $722 | $25917 | $675 | $2795 | $284 |

| Field | SPX | SPY | NDX | QQQ | RUT | IWM |
|---|---|---|---|---|---|---|
| Gamma Tilt | 1.57 | 1.097 | 2.136 | 1.143 | 1.296 | 0.831 |
| Gamma Notional (MM) | $1.126B | $252.557M | $18.03M | $226.641M | $21.104M | -$156.837M |
| 25 Delta Risk Reversal | -0.052 | 0.00 | -0.058 | 0.00 | -0.052 | -0.039 |
| Call Volume | 780.203K | 1.181M | 10.513K | 860.676K | 18.134K | 323.057K |
| Put Volume | 969.071K | 2.238M | 13.48K | 1.404M | 28.975K | 612.648K |
| Call Open Interest | 9.20M | 5.706M | 82.633K | 4.153M | 225.192K | 2.906M |
| Put Open Interest | 13.044M | 13.382M | 89.189K | 6.776M | 423.432K | 7.937M |

## Key Support & Resistance Strikes

- **SPX:** [7000, 7150, 7200, 7300]
- **SPY:** [724, 700, 725, 723]
- **NDX:** [28000, 27000, 26700, 28500]
- **QQQ:** [680, 675, 670, 660]

## SPX Combos (top, with confidence %)

7600 (95%), 7579 (87%), 7550 (85%), 7528 (78%), 7521 (68%), 7499 (99%), 7477 (85%), 7448 (96%), 7433 (79%), 7426 (90%), 7419 (77%), 7412 (79%), **7397 (99.55%)**, 7390 (70%), 7383 (83%), 7375 (96%), 7368 (87%), 7361 (87%), **7354 (99.69%)**, 7346 (78%), 7339 (92%), 7332 (94%), 7325 (99%), 7317 (98%), 7310 (96%), **7303 (99.94%)**, 7296 (93%), 7288 (94%), 7281 (96%), 7274 (98%), 7266 (93%), 7259 (76%), 7252 (97%), 7201 (95%), 7172 (75%), 7158 (75%), 7121 (75%), 7100 (72%), 7020 (83%), 6998 (68%), 6947 (69%), 6918 (70%), 6904 (86%)

## Cross-reference to Hedgeye Risk Range (5/5 SPX)

| Source | Bottom | Top | Today's close |
|---|---|---|---|
| Hedgeye Risk Range (5/5) | buy_trade $7,075 | sell_trade $7,265 | — |
| SpotGamma SPX (5/6 PM) | Pivot $7,180 | Resistance $7,350 / Call Wall $7,300 | — |
| SPX actual | — | — | $7,365 (+1.46%) |

SPX broke above ALL three structural ceilings today: Hedgeye sell_trade $7,265 (top of range), SpotGamma resistance $7,350, SpotGamma Call Wall $7,300. Net is a clean breakout. SpotGamma's PM commentary frames this as Iran-deal pricing + AI earnings beats + oil < $100 = continued stock support through 5/15 OPEX.

For Kristian's playbook: SPX above Hedgeye sell_trade is normally a "trim" signal, but SpotGamma's structural read says continuation is more likely than mean-reversion in this regime. Two frameworks DIVERGE — Hedgeye = trim, SpotGamma = stay long. Decision weighting depends on whether you trust the Quad 2 thesis (Hedgeye) or the structural-flow read (SpotGamma) on this leg.
