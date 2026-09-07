# ml/ — the bot's own ranking model (Round 2, Phase A)

**What the model is for**: a ranker of rule-generated candidates, not a
signal generator. The process rules (bullish TREND, near the low end of the
risk range) decide which bars are even considerable; the model orders those
candidates by expected regime-normalized forward return (`fwd_sharpe_20` =
20-bar forward return / trailing 20-day realized vol). TrendSpider's ML
Quant Lab Round 1 went 24 models / 0 passes (docs/TRENDSPIDER_ML_ROUND1_RESULTS.md)
and taught us the target reframe; regime data is Phase B
(docs/ROUND2_DATA_ROADMAP.md).

## Two rp definitions — do not conflate

- **`ml_features.rp`** (this pipeline): bar D's OWN close vs the range for
  session D, known_at = D 16:00 ET. Right for ranking at the close.
- **`#MFR_<T>_RP`** (TrendSpider export): PRIOR session close vs the range
  for session D, known_at = D 09:30 ET (pre-open). Right for a value that
  must exist before the session trades.

Both are honest; they answer different questions at different clock times.

## How to re-run

```bash
py ml/load_px_daily.py        # px_daily: TV CSV bars + yfinance HYG/^VIX (unadjusted)
py ml/build_features.py       # ml_features (34 features + rv20), spot-checks printed
py ml/build_targets.py        # ml_targets + ml_features.rv20
py ml/candidates.py           # candidate-rule counts (review checkpoint)
py ml/walkforward.py          # walk-forward -> ml_runs row
py ml/report.py               # reports/ml_round2_phaseA_<date>.md + PNGs
```

Feature spec = the TrendSpider scripts in `ml/spec/` (MFR_CORE_ALL.js is
the pooled-model source of truth; clamps ±0.5 bulldist, ±0.2 hi/lo_d3
everywhere). decel comes from `tools.volume_signal`'s own functions; corr
from `tools.relative_strength.pearson`. TLT is TV-indicator end to end
(`source='tv_unverified'`, the 2026-09-06 waiver) — fine for research,
labeled everywhere.

Walk-forward: expanding yearly (test 2021→2026 YTD), 30-bar purge per
ticker at every boundary, never shuffled; shuffled-target LightGBM refit
per fold is the noise band every metric is read against. Training target
winsorized ±5 (COVID rv20 tails); evaluation on raw values.
