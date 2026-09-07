# TRENDSPIDER ML — ROUND 1 SPEC (MFR-only, SPY 60-min) — v1, 2026-09-04

## Purpose

Test one question with TrendSpider's ML Quant (AI Strategy): **does the bot's MFR/SpotGamma feature set have predictive power for a fixed-R/R dip-buy on SPY?** Hedgeye features are deferred to Round 2 (needs the Early Look archive back-parsed; ~40 rows of Hedgeye history is untrainable). If Round 1 finds nothing, Round 2 is not worth building.

Not a "trade like Keith" model. TrendSpider only learns *TP-before-SL within N candles*. Keith is already codified as rules (TREND gate, rp, dip near LRR); this measures whether those rules' *inputs* carry signal.

## Architecture (verified against TrendSpider docs, Sept 2026)

```
hedgeye-bot (Postgres)
   └─ export job: one custom symbol per feature
        POST https://charts.trendspider.com/userapi/1/data/custom_symbols   (NO trailing slash)
        Authorization: Bearer <API key>  ·  Content-type: application/json
        body: {"symbol":"#<SYMBOL>", "fileBase64":"data:text/csv;base64,<b64, no newlines>",
               "targetAssetType":"stock", "groupingMethod":"last"}
        ONE symbol per request — per TrendSpider dev's working example (2026-09-04)
        ⚠ JSON body must have NO whitespace after ':' or ',' — TrendSpider's backend hangs ~60s → nginx 504
          on default json.dumps / requests json= output. Use separators=(",",":"). Root cause of the 09-04 504s.
        STATUS 2026-09-04: backfill uploaded, 16/16 symbols HTTP 200, 1,187 rows; #MFR_SPY_LO renders on chart
        (acceptance #2 ✓). Daily task HedgeyeBotTrendspiderExport 17:45 ET weekdays. Acceptance #3 pending 09-05 run.
        (key lives in the Upload Custom Data dialog: symbol box → custom → Upload custom data → reveal key;
         session = US Equity 09:30–16:00 EST)
        CSV rows:  #SYMBOL,YYYY-MM-DDTHH:mm,value      (America/New_York assumed)
              ↓
TrendSpider custom symbols  (#MFR_SPY_LO, #MFR_SPY_HI, …)
              ↓
Custom JS indicator "MFR_FEATURES" on SPY 60
   request.history("#MFR_SPY_LO","60",{land_onto_current_candles:true}) …
   computes rp etc. on EVERY candle, paints one series per feature
              ↓
AI Strategy inputs = MFR_FEATURES outputs + native indicators + formulas
```

Doc facts that drive the design:
- Upload = CSV, ≤7 MB, 2–3 columns; symbol must start with `#`, alphanumeric, ≤25 chars. Re-upload overwrites matching timestamps and appends the rest (incremental is fine). Timestamps without time default to session open. Data is "landed" onto session candles and **extrapolated forward to the next data point** (= forward-fill step function).
- API upload: "a few calls per minute". Upload-only; no read-back.
- Custom symbols work in AI training data sets and in `request.history()`. They do NOT work in watchlists or scans (irrelevant here).
- `request.history()` output is not auto-aligned to the chart — must pass `{land_onto_current_candles:true}` or call `land_points_onto_series(...)`; use `interpolate_sparse_series` for sparse data.
- Custom JS indicators are valid AI-strategy inputs. Formulas can reference other inputs (`A[1]`, `ma(A,n)`, `stdev`, `log`, ternaries).
- Native "Symbol" parameter on indicators caps at 3 symbols per chart. **Unverified whether `request.history()` calls count toward this** — first test in Step 3 checks it. Fallback: split MFR_FEATURES into 2–3 indicators.

## Step 1 — Bot export job (`trendspider_export.py`)

**Core rule (lookahead):** every row's timestamp = the moment the bot *knew* the value, NOT the bar it describes. MFR ranges computed at the EOD run get the run's wall-clock time (e.g. `2026-09-03T16:45`). SpotGamma AM levels get the 5:30am canary time. Anything stamped earlier than it was actually available makes the backtest fiction. Store `known_at` in the export table; never derive it from the market date.

**Export cadence:** daily after the EOD run + after the SG canary. Full-history backfill once (one CSV per symbol, or batched — rate limit is a few calls/min so batch ~10 symbols per file; 3-column format allows mixed symbols in one CSV).

**Symbols to export (Round 1). Export LEVELS, not ratios, wherever a ratio depends on live price — TrendSpider computes the ratio per candle.**

| Symbol | Value | Source table | Cadence | Notes |
|---|---|---|---|---|
| `#MFR_SPY_LO` | MFR range low, SPY | mfr ranges | EOD | level; rp computed in JS |
| `#MFR_SPY_HI` | MFR range high, SPY | mfr ranges | EOD | level |
| `#MFR_SPY_TREND` | +1 bullish / 0 neutral / −1 bearish | mfr trend | EOD | the TREND gate |
| `#MFR_SPY_HURST` | Hurst 0–1 | mfr | EOD | |
| `#MFR_SPY_DECEL` | decel-volume streak, days (cap 7) | volume module | EOD | 0 when none; −1 if distribution flagged |
| `#MFR_SPY_IVPD` | ivpd RAW level (percentile is NOT stored — computed in TS JS via 60-session min-max) | mfr_snapshots.full_payload ivpd · fetched_at | EOD | Step 0 decision 2 |
| `#MFR_VIX_LO` / `#MFR_VIX_HI` | VIX MFR range | mfr_snapshots ticker=`^VIX` (caret) · fetched_at | EOD | VIX rp per candle needs VIX price → JS pulls VIX from TS (confirm TS spelling on chart) |
| `#MFR_VIX_TREND` | +1/0/−1 | mfr_snapshots · fetched_at | EOD | vol trend sign |
| `#RS_SPYUNIV_CORR60` | 60d avg pairwise sector corr | diversification_snapshots (60d, sector_spdr) · computed_at | EOD | 0–1 |
| `#RORO_HYGTLT_ROC` | HYG-vs-TLT 10-session ROC (`rs_trade`) — the ratio LEVEL is not stored | rs_snapshots · computed_at | EOD | Step 0 decision 3 |
| `#MFR_SPY_ZG` / `#MFR_SPY_CW` / `#MFR_SPY_PW` | MFR zero-gamma / call wall / put wall for SPY | mfr_snapshots · fetched_at | EOD | **replaces SpotGamma** — SG corpus dead since 2026-08-06 with a one-shot backfill stamp (no honest known_at); SG excluded from Round 1, 5:30am run dropped |
| `#QUAD_M` / `#QUAD_Q` | 1–4 | quad_regime_history · effective_at | on change | categorical; expect low importance (few regime changes in 2 months) |

Step 0 rules (2026-09-04): `trend_signal=trendError` → skip row (never emit 0). Dedupe on `(symbol, snapshot_date)`; `fetched_at` is bumped on re-fetch upserts so known_at may be *later* than first-known — safe direction, logged. Single scheduled run after the EOD job.

Skip in Round 1: RS rank (SPY is the benchmark — rk is undefined for it), cSPY/cUUP (SPY is SPY), BOOK RISK (not a market feature).

**Validation before first upload:** print min/max/nulls per symbol; no NaN, no zeros where a level is expected; all timestamps strictly ≤ now and monotonic per symbol. Corpus-first rule applies: the export table (`ts_export_log`: symbol, known_at, value, uploaded_at, http_status) lands in Postgres before the job is "done".

## Step 2 — Custom JS indicator `MFR_FEATURES` (SPY 60)

Outputs (one painted series each; all bounded):

```
rp        = (C - LO) / (HI - LO)                  // clamp to [-0.5, 1.5]
trend     = TREND                                 // -1/0/+1
hurst     = HURST
decel     = DECEL / 7                             // 0–1, or -0.14 for distribution
ivpd_pct  = (IVPD - min(IVPD,420)) / (max(IVPD,420) - min(IVPD,420))  // ~60 sessions of 60-min bars; 0–1
vix_rp    = (VIXclose - VIXLO) / (VIXHI - VIXLO)  // clamp [-0.5, 1.5]
vix_bucket= VIX<20 ? 0 : VIX<=30 ? 1 : 2          // Hedgeye buckets
vix_trend = VIXTREND
corr60    = CORR60
roro_roc  = RORO_ROC                               // stored 10-session ROC, already a change feature
flip_dist = (C - ZG) / C                          // signed % above/below MFR zero-gamma
wall_pos  = (C - PW) / (CW - PW)                  // 0 = at put wall, 1 = at call wall; clamp
quad_m, quad_q
```

Implementation notes: `Promise.all` the `request.history` calls; `land_onto_current_candles:true` on each; guard divide-by-zero (HI==LO → output NaN → TS treats as missing; better to output the previous value). Every output must stay bounded — no raw price levels leak out (TS will refuse them as inputs anyway).

**Test in Step 3 first:** add the indicator to a SPY 60 chart and confirm all series paint back to the start of the export history with no gaps. If it errors on the symbol count, split into `MFR_FEATURES_A/B/C`.

## Step 3 — AI Strategy setup

- **Market:** SPY, 60-min. The docs want ~2,000 candles held back for the final backtest; that is impossible here — ~2 months × ~7 bars/day ≈ 300 candles total. **That is thin.** Train on everything except the last ~25% and backtest on that tail via Strategy Tester; accept that Round 1 is a *feasibility* test, not a conclusive one. Re-run monthly as the corpus grows; the model gets real at ~1,500+ candles (≈ 9 months).
- **Signal (fixed R/R, mirrors a TRADE-duration dip-buy):** horizon 20 candles (~3 sessions), TP 1.0%, SL 0.5% on SPY 60-min, conservative mode (SL never hit). Tune until yellow candles are common-but-not-everywhere; if too rare, TP 0.8/SL 0.4.
- **Inputs, round 1a (the confluence, ~9 inputs):** rp, trend, hurst, decel, vix_rp, vix_bucket, corr60, roro_roc, flip_dist. Plus native RSI(14) and `(RSI − RSI[1]) / RSI[1]` as controls — if RSI dominates importance, the MFR set isn't adding much.
- **Inputs, round 1b (feature-engineered):** add `rp − ma(rp, 20)` (mean-extracted), `ma(decel, 5)`, `wall_pos`, `ivpd_pct`, quad_m/quad_q.
- **Models:** 5× KNN, 5× RF per input set. Then pick top 3 of each type, crossbreed, one fresh model per generation, 3 generations.
- **Quality gates (from the docs' own criteria):** dots must sit outside the random-signal dotted band; gray vertical lines ≤ ~5% apart; chain slopes up-right; no pale-red regression line. Anything failing two of these is discarded. Report input importance for survivors.
- **Backtest:** deploy the survivor → Strategy Tester on the held-out tail only. Also run "enter on signal, exit when signal disappears + 0.5% stop" as the second personality.

## Deliverable of Round 1

One doc in the project: `TRENDSPIDER_ML_ROUND1_RESULTS` — per model: input set, model type, confidence chosen, signals / win% on test, importance ranking, held-out backtest P&L and DD. Decision rule: if the best model's win% at ≥50% confidence beats random + 5pts on the held-out tail, greenlight Round 2 (Hedgeye layer). If not, the MFR features don't carry signal at this horizon and Round 2 is a Hedgeye-archive scraping project with no evidence behind it — park it.

## Open items / risks

1. `request.history` vs the 3-symbol cap — verify empirically (Step 2 test).
2. TS symbol string for VIX — confirm on the chart before hardcoding in JS.
3. Sample size — 300 candles is a smoke test. Do not size any real position off Round 1.
4. Regime — the whole training window is one tape. Check the random band before believing green dots.
5. Bot export must read from the *stored* corpus, never recompute levels at export time (otherwise known_at is a lie).

## Sources
- TrendSpider KB: Uploading Custom Data; AI Strategies ch. 1–6; Indicator Alternative Symbol
- TrendSpider scripting docs: `additional_series` (request.history / land_points_onto_series), `fetching_data_from_apis` (request.http)
