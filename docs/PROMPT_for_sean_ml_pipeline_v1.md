# Claude Code brief — hedgeye-bot `ml/` : our own ranking model (Round 2, Step 1)

Context: TrendSpider's ML Quant Lab is done — 24 models, 0 passes (see TRENDSPIDER_ML_ROUND1_RESULTS.md in the trading-desk project). Two things it did tell us: (1) the MFR features consistently out-rank RSI, and in the pooled model **distance to the TREND line was the #1 feature**, range dynamics #2, Hurst mid-pack; (2) a fixed "TP before SL in N days" target on daily bars is the wrong question. We are moving the ML into the bot, in Python, with a reframed target: **the rules generate candidates, the model ranks them.** Disable the `HedgeyeBotTrendspiderExport` scheduled task (keep the code). This brief is Phase A only — a working, honest pipeline on the data we already hold. Regime data (FRED Quad, CBOE vol) is Phase B, in ROUND2_DATA_ROADMAP.md.

Same doctrine as everything else in the repo: corpus-first, honest `known_at`, no lookahead, Python does the arithmetic, stop for review at the checkpoints.

## Universe & data (all already in Postgres)
Tickers: SPY, USO, UUP, AAAU, TLT (8 years each). Sources:
- `tv_mfr_history` — Range High/Low, LT range, bull/bear levels (TLT: indicator-only, feed_verified=false — fine for research; label the rows).
- `tv_features_history` — hurst64, hurst256, trend_lvl, trade_lvl, buy/mega_buy/sell/mega_sell, volatility, vixfix, up_t1/down_t1, trade2/trend2 (opaque).
- OHLCV: the TradingView CSVs in data/tradingview/ carry open/high/low/close/volume for the five tickers. For HYG and ^VIX daily closes (needed for context features) pull free daily history (Stooq or yfinance — your call), store in a `px_daily(ticker, bar_date, o,h,l,c,v, source)` table, and note the source. VIX: use the index itself, not the range midpoint proxy we had to use in TrendSpider.
- Live period: union with mfr_snapshots / feature tables after 2026-05-15 the same way trendspider_export.py does. Keep the split explicit in a `source` column.

## Step 1 — feature table `ml_features(ticker, bar_date, known_at, <features>)`  → stop for review
Every feature at bar D uses data with known_at ≤ D 16:00 ET only (ranges dated D are known 09:30 D — allowed). One row per ticker per bar. Features — port exactly the definitions from the TrendSpider scripts in the project (`MFR_CORE_ALL.js`, `MFR_SPY_CORE_v3.js`, `MFR_USO_CORE_v2.js`), they are the spec:
- state: rp (clamped −0.5..1.5), rp_dev20, bulldist (clamp ±0.5), rng_width, ltrp, hurst64, hurst256, trend_dist = (close−trend_lvl)/close, trade_dist, above_trend (0/1), vixfix, volatility, mega_buy/sell/mega_sell flags (and buy)
- rate of change: rp_d3, bulldist_d3, hi_d3, lo_d3 (range HH/LH, HL/LL), trend_dist_d3, hurst64_d5, rng_width_d5
- volume: decel streak & distribution flag — reuse volume_signal.py directly, don't re-port
- context (same for every ticker on a date): vix_level, vix_bucket (10–19/20–30/>30), vix_roc5, hyg_roc10, uup_rp, uup_rp_d3
- cross-asset per ticker: corr30 of daily returns vs SPY and vs UUP (trailing 30, need ≥20 obs), usd_pressure = −corr(UUP) × (uup_rp − 0.5)
Validation before review: row counts per ticker, NaN share per feature, min/max, and a spot-check of 3 dates against the TrendSpider script values I logged (USO 2026-09-04: rp 0.717, hurst64 0.75, hurst256 0.66, trend_dist 0.103; TLT 2026-09-04: rp 0.445, hurst64 0.70, hurst256 0.61, trend_dist −0.008).

## Step 2 — targets `ml_targets(ticker, bar_date, <targets>)`
Computed from bars strictly after D (close D as entry reference):
- `fwd_ret_10`, `fwd_ret_20`, `fwd_ret_30` — simple forward close returns
- `fwd_mfe_20`, `fwd_mae_20` — max favorable / adverse excursion over 20 bars (from highs/lows)
- `rr_hit_5_2p5_30` — the TrendSpider target (TP 5% before SL 2.5% in 30 bars, conservative) — kept ONLY as a continuity check; we expect it to fail like it did there
- `fwd_sharpe_20` = fwd_ret_20 / realized 20d vol at D (regime-normalized return — this is the primary ranking target)

## Step 3 — candidate rules (the thing the model ranks)
A candidate row = a bar where the process would even consider a long. Define `setup_dip`:
above_trend == 1 AND rp < 0.35 AND decel_streak ≥ 2 (no distribution flag). Also `setup_any` = every bar (control). Report candidate counts per ticker per year. If setup_dip yields < 300 rows total, loosen rp to < 0.45 and say so.

## Step 4 — models, walk-forward only
- Splits: expanding window by calendar year. Train ≤ 2020 → test 2021; train ≤ 2021 → test 2022; … → test 2025; final train ≤ 2025 → test 2026 YTD. Purge 30 bars at each boundary (no label leakage across the split). Never shuffle.
- Models: LightGBM regressor on `fwd_sharpe_20` (primary) and LightGBM classifier on `fwd_ret_20 > 0`; logistic regression as the linear baseline; **a random-permutation baseline** (shuffle the target within each train fold, refit, score) — this is our version of TrendSpider's random band and every metric is reported relative to it.
- Fit once on `setup_any`, once on `setup_dip`. Pooled across the five tickers with ticker as a categorical feature; also report per-ticker.
- Metrics per test year and overall: Spearman IC (prediction vs realized fwd_sharpe_20), AUC for the classifier, and the one that matters: **mean fwd_ret_20 and hit rate of the top-decile predictions vs the base rate of all candidates**, plus a simple equity curve of "take the top-decile setups, hold 20 bars, equal weight" vs "take all setups". Feature importance (gain) per fold.
- Pass/fail we agreed on the desk: the top-decile of `setup_dip` must beat all-setups by ≥ 5 pts hit rate AND positive IC in ≥ 4 of 6 test years, and the random baseline must not. Don't tune to hit it — report what it is.

## Step 5 — deliverables
- `ml/build_features.py`, `ml/build_targets.py`, `ml/walkforward.py`, `ml/report.py`; tables `ml_features`, `ml_targets`, `ml_runs` (run id, params, fold metrics, importance JSON); output `reports/ml_round2_phaseA_<date>.md` with the tables and PNG equity curves.
- README section: how to re-run, and a one-paragraph "what the model is for" (ranker of rule-generated candidates, not a signal generator).
- Commit + push. Paste the Step 1 validation, the candidate counts, and the final report into chat.

Stop for review after Step 1 and after Step 3 (candidate counts) before training anything.
