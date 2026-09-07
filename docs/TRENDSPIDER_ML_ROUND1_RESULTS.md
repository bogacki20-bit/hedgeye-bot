# TRENDSPIDER ML — ROUND 1 RESULTS (running) — updated 2026-09-06

Decision deadline: Fri 2026-09-18. Rule: worth keeping if ≥1 model per direction on ≥1 asset beats the random band by 5+ pts win rate at a confidence level that still yields >~30 signals on held-out 2025–26, gray lines ≤~5% apart. SPY-long-only passing = drift, not signal.

## Setup (all models)
SPY daily · train 2019-03-01 → 2024-12-31 (1.5K candles) · signal: TP 3% / SL 1.5% / 15 candles, conservative (R/R 2.0, breakeven 33%) · KNN grid-search · held-out = 2025-01 → now.

## Models
| # | name | inputs | test-slice result | importance | out-of-sample (Strategy Tester) |
|---|---|---|---|---|---|
| 1 | SPY_L_M1_KNN_base | rp, RSI14, trend, rp_dev20 (1 script) | healthy chain; 15 sig @41% win (≥36% conf); 8@51%, 4@52%; low-conf end at top of random band (26–35%) | trend 100, rp_dev20 75, rp 63, RSI 63 | 300 candles (Jul25→Sep26) @36%: 4 trades, 2W/2L, +3.0% net, exp +0.5, maxDD −4.0% (SPY +26%) |
| 2 | SPY_L_M2_KNN_bulldist | rp, RSI, trend, rp_dev20, decel(chart vol), spy_bulldist, spy_ltrp, vix_ltrp (4 scripts) | 10 sig @53% (≥36%), 6@62%; low-conf end at random-band edge (23–32%) | bulldist 100, RSI 60, decel/rp_dev20/vix_ltrp 40, rp 20, trend & spy_ltrp uncertain | **0 trades at any confidence — runtime limit, see below** |
| 3 | SPY_L_M3_KNN_core | rp, RSI, rp_dev20, decel, bulldist, spy_ltrp, vix_ltrp — ALL from single script MFR_SPY_CORE | chain up-right; 37 sig @~40%, 18 @41% (green); low-conf end at random-band edge | rp 100, bulldist 88, spy_ltrp 82, rp_dev20 71, RSI 41, vix_ltrp 29, decel 6 | 300 candles @29% AND @10%: **2 trades**, 1W (+0.7%, timed out) / 1L (−1.5%), −0.8% net, exp −0.3 (SPY +26%) |

| 4 | RF SPY,D "Secret fierce rocket" | same 7 inputs (MFR_SPY_CORE) — Random Forest, 200 trees, depth 20 | flat chain 36–42%; 10 sig @42% at 50%+ conf | bulldist 100, rp_dev20 52, RSI 40, rp 36 | 300 candles @50%: **12 trades**, 5W/7L (42%), avg win +2.0% / avg loss −1.5%, net −1.0%, exp 0.0, maxDD −8.6% |
| 4b | NB / LR (same inputs) | — | NB: RSI 100% dominant, 36% at every threshold (finds nothing beyond RSI). LR: RSI 100, rp_dev20 88, spy_ltrp 84; 17 sig @39% at 57% | | not backtested — weaker than RF on the slice |

Script fix 2026-09-06: MFR_SPY_CORE v2 drops custom-symbol points dated after the last chart candle (weekend-stamped export rows caused "for_every: all series must have equal length" on the chart). M3 re-run after fix: identical 2 trades.

## Findings
1. Custom-script outputs ARE selectable as ML inputs; Price Compare is NOT offered. Scripts are the only path for custom symbols.
2. **Strategy runtime limit:** a strategy can make ≤6 request.history calls TOTAL. Model 2 pulled 4 scripts (24 calls) → inputs empty → zero trades. Model 1 (1 script, 6 calls) ran. Rule: every model's inputs must come from ONE script with ≤6 symbol fetches. Built `MFR_SPY_CORE` (5 fetches: SPY LO/HI/BULLDIST/LTRP + VIX LTRP; computes rp, rp_dev20, bulldist clamped ±0.3, decel from chart volume).
3. `spy_bulldist` (distance to MFR bullish trend level) dominates; plain trend flag becomes redundant beside it. Decel (ported volume rule) carries weight. Plain rp is weak alone; rp_dev20 (mean-extracted) is better.
4. Live-only symbols (#MFR_SPY_HURST/IVPD/DECEL) can't train on 2019–24 — expected. #SHADOW_SPY_HURST and #CORR_SPY_UUP60 load on chart but fail in the ML/strategy runtime → Sean ticket (features_backfill export formatting).
5. Both models: low-confidence signals sit at the edge of the random band; edge only appears ≥36% confidence. Signal count out-of-sample is thin (4 in 14 months for M1) — well under the ~30 the decision rule wants.

6. **Regime gap (day-1 headline):** models that fire ~100× on the 2023–24 test slice fire 2–4× on Jul-2025→Sep-2026 regardless of confidence gate. Runtime is now proven (single script). The OOS tape doesn't resemble the training distribution through these features (rp/bulldist pinned high in an uninterrupted climb). Long-SPY KNN: 0 passes of the decision rule so far.

## Short side — how (2026-09-06)
TrendSpider ML is **long-only**: the signal is always "hit TP% before SL%" (a negative TP is rejected; docs/blog confirm no short mode). Workaround: train on the **inverse ETF SH (ProShares Short S&P500), daily** — SH +3% before −1.5% in 15 candles ≡ SPY −3% before +1.5%. To keep the features about SPY (not SH), new script `MFR_SPY_CORE_INV` fetches SPY close/volume via `request.history('SPY','D')` and computes the same 7 features from that (SPY + 5 custom symbols = 6 fetches, the runtime limit). RSI(14) control is RSI of SH (≈ mirror of SPY's). Training window identical to the long models (2019-03-01 → 2024-12-31, 1.5K candles). Random control band 14–24% — the short outcome is rarer, so any edge shows against a lower bar. The deployed strategy is a LONG on SH; in practice you'd express it as short SPY / puts.

| S1 | SH,D KNN (=SPY short) | RSI(14 of SH) + 6 from MFR_SPY_CORE_INV | **NO GO** (TrendSpider's own tag): 7 dots all inside random band (13–19% win vs 14–24% random); chain INVERTED — 2 sig @49% conf & 5 @39% win least, 49 sig @5% win most; pale-red regression line | vix_ltrp 100, rp 52, rp_dev20 24, decel 7 (RSI/bulldist/spy_ltrp dropped) | not deployable |
| S2 | SH,D RF (same inputs) | 200 trees, depth 20 | **NO GO**: inverted chain, 10–17% win (below the 14–24% random band at the high-confidence end: 6 sig @37% conf → 12%) | vix_ltrp 100, rp 94, rp_dev20 50, spy_ltrp 44 | not deployable |
| S3 | SH,D NB (same inputs) | | **NO GO**: flat 17% at every threshold (random band 14–24%) | spy_ltrp 100, vix_ltrp 32, rp 13, RSI 11 | not deployable |
| S4 | SH,D LR (same inputs) | C 100, liblinear | "go backtest" tag, but it's ONE dot: 4 signals @71%+ conf at 40% win (=1–2 wins); the rest of the chain (5→49 sig) sits at 11–19% = random | spy_ltrp 100, bulldist 87, vix_ltrp 86, rp_dev20 59 | not deployed — 4-signal dot is noise, not backtestable |

**Short-side verdict (2026-09-06):** 0/4 model types find a SPY-selloff signal in these features at −3%/+1.5%/15 cdl. Consistent tell across all four: the LT-range positions (spy_ltrp / vix_ltrp) dominate importance on the short side while bulldist/rp dominate on the long side — the features "know" a selloff regime differently, but not well enough to time a 3% drop in 15 days. Note the fixed-R/R target may be the wrong shape for selloffs (fast, larger, mean-revert) — the wider +5%/−2.5%/30-cdl test applies to SH too.

## Wide target (TREND-duration: TP 5% / SL 2.5% / 30 cdl) — 2026-09-06
| W1 | SPY_L_M4_KNN_wide (deployed) | same 7 (MFR_SPY_CORE), KNN k=50 | **best test-slice chain of the round**: 118 sig @40%, 102 @40%, 87 @41%, 63 @41%, 28 @44%, 10 @48%, 4 @58% — whole chain in the green, above the 20–40% random band, slopes up-right, gray lines tight | bulldist 100, vix_ltrp 88, RSI 63, rp 38 | 300 cdl (Jul25→Sep26) @1%: **1 trade**, 1W +5.3% (Jun-26), maxDD −1.1% (SPY +26%). Regime gap again. |
| W2 | SH,D KNN wide (=SPY short) | same 7 (MFR_SPY_CORE_INV) | **NO GO**: 6–20% win, inverted (6 sig @33% conf → 6% win) | rp 100, rp_dev20 52, spy_ltrp 48, vix_ltrp 41 | not deployable |

Short-side final: 0/5 across both target shapes. Long-side: the wide target is the one model whose test-slice chain is convincingly above random, but it does not fire on the 2025–26 tape.

## What we're NOT giving it (diagnosis, 2026-09-06)
1. No rate-of-change inputs — every feature is a state (rp, bulldist, ltrp). Keith's process is ROC; the model gets no Δrp/Δbulldist/Δvix, no HH/HL vs LH/LL range dynamics.
2. No VIX level (only VIX range position) — the 10–19/20–30/>30 bucket gate is absent.
3. No credit/RORO — HYG/TLT & HYG/LQD only have 2 months in the bot; TrendSpider has HYG/TLT natively (script can compute ROC itself).
4. No Quad, no SpotGamma — Keith's EDGE layer; only the TIMING layer has been tested.
5. Hurst / ivpd broken symbols (Sean).
6. 1.5K daily candles is thin; docs want 2K+ plus 2K held out.
Next: MFR_SPY_CORE_v3 — swap LO/HI for #MFR_SPY_RP (frees a fetch), add ^VIX level + HYG/TLT 10d ROC (native), add 3-day deltas of rp/bulldist/vix_ltrp computed on-chart. ~11 inputs, half ROC. No Sean dependency.

## v3 — rate-of-change feature set (2026-09-06, script `MFR_SPY_CORE_v3`)
Inputs (13 + RSI control): rp, rp_dev20, rp_d3, bulldist, bulldist_d3, hi_d3, lo_d3 (range dynamics = HH/HL vs LH/LL), rng_width, vix_level (proxy = midpoint of bot's VIX range; $VIX index not reachable from request.history), vix_bucket (10–19/20–30/>30), vix_roc5, hyg_roc10 (credit RORO; TLT dropped for fetch budget), decel. Wide target (+5%/−2.5%/30 cdl), SPY daily, 2019-03→2024-12.
| V1 | SPY_L_M5_KNN_v3wide (deployed) | KNN k=20 | 118 @42% → 93 @43 → 69 @44 → 36 @47 → 28 @48 → 14 @55 → 9 @68 → 4 @60. Steeper than W1. | RSI 100, decel 80, rp_dev20 80, bulldist 60, rp 50, vix_level 50, lo_d3 20, rng_width 20; vix_roc5/rp_d3/bulldist_d3/hi_d3/vix_bucket/hyg_roc10 "uncertain" | 300 cdl: **7 trades, 2W/5L (29%), −5.1% net, maxDD −11.9%** — identical at conf 1/30/50 (model was ≥50% confident on all 7). Losses cluster Feb–Apr-26 selloff (bought dips that kept falling). |
| V2 | SPY_L_M6_RF_v3wide (deployed) | RF 200 trees | **best slice chain of the study**: 118 @42 → 100 @45 → 69 @47 → 41 @47 → 25 @55 → 20 @62 → 10 @62 → 7 @78 | bulldist 100, **hi_d3 92** (ROC feature ranks #2), RSI 81, rp 65 | 300 cdl @1–50%: 5 trades, 2W/3L, −2.3%, maxDD −7.6%, avg win only +2.7% (timed out). @65%: **1 trade, lost −3.1%** (Mar-26). |
| V3/V4 | NB / LR v3 | | NB flat 39% at every threshold (rng_width 100, vix_level 68). LR 118 @41% flat then 43 @46, 17 @72 (RSI 100, vix_bucket 63, vix_roc5 45, hyg_roc10 37) | | not backtested — weaker than RF on slice |

**v3 verdict:** the ROC inputs did exactly what they were meant to — the models now *fire* on the 2025–26 tape (7 and 5 trades vs 1 for W1) and one ROC feature (hi_d3) ranks #2 in RF. But the signals it fires are net losers, and the *higher* the confidence gate the *worse* the OOS outcome (RF @65% = 1 trade, a loss). That is the signature of a model that learned the 2019–24 dip-buy regime and is confidently wrong in a different one. Slice edge ≠ tradable edge.

## USO — non-SPY control (2026-09-06, script `MFR_USO_CORE` = v3 feature set on USO's own ranges; VIX proxy + HYG kept as macro context)
Why USO not QQQ/TLT: needed an asset whose 2019–24 training window contains BOTH regimes (2020 crash, 2021–22 bull, 2023–25 chop) and that is ~uncorrelated to SPY; QQQ ≈ SPY; TLT not yet ingested. Caveat discovered after the fact: USO was **+89% over the Jul-25→Sep-26 test window**, so OOS is still a bull tape.
| U1 | USO_L_M1_RF_v3wide (deployed) | RF 200 trees depth 10 | hockey stick: 74→12 signals all flat ~28% (inside random band), then 7 @58% and 5 @63% at 43%+ conf. ROC features rank high: vix_roc5 61, bulldist_d3 61, hi_d3 50, hyg_roc10 50, rng_width 50; bulldist 100, RSI 94 | | 300 cdl @43%: **10 trades, 4W/6L (40%), +0.8% net**, avg win +5.1 / avg loss −3.1 (R/R 1.63), maxDD −12.6%, exp +0.1 (USO +89%) |
| U2–U4 | USO KNN / NB / LR | | all **NO GO**: LR flat 23–26% (RSI 100, vix_bucket 93); KNN 24–28% (vix_bucket 100, RSI 17, rest uncertain); NB inverted 9–28% (vix_bucket 100, vix_level 44) | | not deployable |

**USO verdict:** RF is the only model type that separates on oil, and only for its top ~7 signals; OOS it is breakeven (+0.8%, 40% win, R/R 1.6) on a tape where oil rose 89%. Same pattern as SPY: slice edge at the top of the confidence chain, nothing tradable forward. The one novelty: on oil the ROC features (vix_roc5, bulldist_d3, hi_d3, hyg_roc10) out-rank the state features — the v3 inputs are the right *kind* of input, there just isn't enough of them / enough history.

## USO v2 — cross-asset context (Kris's ask, 2026-09-06): "is the dollar at the top of its range while inversely correlated to oil?"
Gap admitted: USO v1 had VIX+HYG as context (stock-market variables), NOT the dollar's range or the USD↔oil correlation. Script `MFR_USO_CORE_v2` re-spends the 6 fetch slots for oil: USO LO/HI/BULLDIST + #MFR_UUP_RP + UUP price + SPY price. (#CORR_UUP_USO30/90 export no_data → same Sean formatting bug as #CORR_SPY_UUP60; correlations computed on-chart instead: trailing 30d Pearson of daily returns, no lookahead.) #MFR_UUP_TREND dropped for the SPY slot.
New features: uup_rp, uup_rp_d3, usd_oil_corr30, spy_oil_corr30, spy_roc10, **usd_pressure = −corr(USD,oil) × (uup_rp − 0.5)** (>0 = dollar extended high AND inverse → bullish oil; the exact setup Kris described), usd_pressure_d3. 17 inputs incl. RSI control. RF, wide target, USO daily 2019–24.
| U5 | USO v2 RF (17 inputs: dollar + SPY context) | RF 200 trees | **NO GO**: flat 24–31% across 74→6 signals, inside random band. Importance: **uup_rp 100, usd_oil_corr30 67, rp_dev20 33, spy_oil_corr30 33, spy_roc10 33**; everything else incl. usd_pressure "uncertain" | | not deployable — the v1 hockey stick (7 sig @58%+) disappeared once the cross-asset inputs were added |
| U6–U8 | USO v2 KNN / LR / NB | | all **NO GO**: KNN 21–35% (rp_d3 100, rp_dev20 76, bulldist 57, spy_oil_corr30 48; only 4 signals at the top); LR flat 23–26% (spy_oil_corr30 100, rest uncertain); NB inverted 18–27% (decel 100, RSI 94, rp_dev20 87, uup_rp_d3 59) | | not deployable |

**Cross-asset verdict:** the models *do* pick up the dollar (uup_rp and the USD↔oil correlation are the top-2 RF features), but it doesn't translate into a win-rate chain — adding 4 cross-asset inputs diluted the one thing that worked on oil (RF's top-7 signals). The interaction feature usd_pressure ("dollar high in range AND inverse to oil") was not used by any model. Note the setup Kris described is a discretionary, low-frequency one (a handful of episodes in 6 years); a fixed-R/R classifier on daily bars isn't the tool that finds it. Correlations are now computed on-chart (30d Pearson) since #CORR_* symbols export no_data.

## New TradingView export (USO, 2026-09-06 PM) — richer MFR column set
Kris re-exported USO with the full indicator: 45 columns incl. **Hurst Exponent 64 & 256** (0.54–0.85, full 8y history — the broken shadow-hurst gap), **Trend / Trade price levels** (close > Trend 63% of days; likely the TRADE/TREND lines = Keith's duration gate), **Mega Buy / Sell / Mega Sell flags** (53/109/99 firings; plain Buy all zero), Up/Down Target 1&2, Period Open, Volatility, Vixfix, Period 1–3 oscillators; 52-week hi/lo columns empty. Needs Sean to ingest (tv_features_history) and export as #MFR_<T>_HURST64/256, _TRENDLVL, _TRADELVL, _MEGABUY, _SELL, _MEGASELL, _VOL, _VIXFIX under the same known_at doctrine — for every asset Kris re-exports (SPY, UUP, VIX, AAAU, TLT). Hurst + distance-to-TREND are the two to put in the next model.

## Round-2 symbols — blocked on TrendSpider (evening 2026-09-06)
Sean's 81-symbol upload (Hurst64/256, TRENDLVL/TRADELVL, BUY/SELL flags, TLT range set) all HTTP 200 at ~16:40 ET; #MFR_TLT_LO renders on a chart. But from a custom script, `request.history()` on ANY round-2 symbol (#MFR_USO_HURST64, #MFR_TLT_LO, #MFR_SPY_TRENDLVL) hangs to "Request timeout" and leaves the engine "not responding" for the next call — at 17:50 ET and again 20:12 ET — while round-1 symbols answer in <50 ms. Distinct from the CORR fault (instant no_data). Both go in Sean's TrendSpider ticket. Script `MFR_CORE_ALL` (generic per-ticker: rp/ROC set + hurst64/256 + trend_dist/above_trend + decel, 6 fetches) is written and saved; the pooled SPY+USO+UUP+AAAU+TLT model runs as soon as the symbols are readable.

## Pooled multi-asset model — the last TrendSpider test (2026-09-07 09:10 ET)
Round-2 symbols became readable overnight (TrendSpider indexing lag ~16h; `#MFR_SPY_TRENDLVL` 248 ms at 07:32). `MFR_CORE_ALL` verified on USO and TLT (ticker auto-detect, 6/6 symbols). Wizard caps at **3 markets** and RF grid-search refuses with 3 markets ("unlikely to converge"), so: **SPY + USO + TLT daily**, 2019-03→2024-12 (~4,400 bars), RF fixed 200 trees / depth 10 / entropy, wide target, 16 inputs (RSI + 15 MFR_CORE_ALL incl. hurst64/256, trend_dist, above_trend).
| POOL1 | RF SPY+USO+TLT | **NO GO**: 158 sig @16–18% → 105 @22% → 39 @28% → 3 @28%; entire chain below the 33% breakeven | **trend_dist 100**, rng_width 82, trend_dist_d3 75, rp 60, bulldist 58, hi_d3 57, rp_dev20 50, hurst256 45, above_trend 44, bulldist_d3 44, RSI 28, hurst64 26, lo_d3 23, rp_d3 16, hurst64_d5 14, decel 6 | not deployable |

Read: pooling three regimes did what it should for *feature ranking* — every MFR feature now out-ranks RSI, and distance-to-TREND (Keith's duration gate) is the single most important input — but the win-rate chain is flat and sub-breakeven. More data and the right features still don't make a 30-day fixed-R/R classifier work on daily bars.

**ROUND 1 FINAL: 24 models, 0 passes.** Long SPY 0/10, short SPY 0/5, long USO 0/8, pooled 0/1. TrendSpider ML Quant Lab is not a signal source for this process. Decision for 9/18: keep TrendSpider only if the charting / MFR-on-every-asset visibility justifies it on its own.

## Day-1 verdict (2026-09-06)
Long SPY, 4 model types × 3 input sets: every model shows a small real edge on the training-era test slice with MFR features out-ranking RSI, but out-of-sample (Jul-25→Sep-26) the best is M1 (+3%, 4 trades) and the rest are flat/negative vs SPY +26%. Decision rule: 0 passes. Not yet a "no" — long-only on a grinding tape is the weakest possible test.

## Next (in trust order)
1. ~~SPY SHORT model~~ DONE — 0/4 (see short-side verdict).
2. TLT long + short (Kris exported BATS_TLT_1D; Sean to ingest → new script MFR_TLT_CORE; short via TBF the same way as SH).
3. Wider target (+5%/−2.5%, 30 cdl = TREND-duration) on BOTH SPY long and SH — can run now, doesn't need Sean.
Scorecard vs decision rule (end of day 2026-09-06): long SPY 0/10 (M1–M4 + W1 + V1–V4), short SPY 0/5, long USO 0/8 (v1 + v2 cross-asset). 23 models, 0 passes. Nothing has beaten the random band by 5 pts on held-out 2025–26 with >30 signals; the best OOS is M1 (+3%, 4 trades) and the highest-conviction models lose.

## Honest read going into the 9/18 decision
What's been proven: the plumbing works (bot → custom symbols → single script → ML → strategy tester); MFR features carry a small edge on the training era; adding rate-of-change makes models fire in the live regime. What has NOT appeared: a model whose confident signals win out of sample. Two things left that could change the answer, both outside what TrendSpider can do on its own: (1) a non-bull asset (TLT) — tests whether the edge is SPY-drift; (2) the Quad / Hedgeye layer, which has 2 months of history and can't be trained. If TLT is also flat, the fair conclusion is that TrendSpider ML is a charting/visualization convenience, not a signal source for this process.
- Sean: fix the two broken feature symbols; RP live-close source; SCREEN rp bug (before Tue open); runner-clone dirty tree.
