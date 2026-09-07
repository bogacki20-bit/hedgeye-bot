# MFR_SPY_CORE_v3 / MFR_USO_CORE — spec deltas vs MFR_CORE_ALL.js

Retrieved 2026-09-07 from the TrendSpider Custom Indicators editor
(MFR_SPY_CORE_v3: 5,145 chars; MFR_USO_CORE "v3 feature set on USO": 5,167 chars).
Both share MFR_CORE_ALL's plumbing (land/feat/lag/pct/diff), rp, rp_ma20/rp_dev20,
rng_width, rp_d3, bulldist_d3 and the identical decel block. Differences that matter
for the Python port:

## Fetches
Six per script: `#MFR_<T>_LO`, `#MFR_<T>_HI`, `#MFR_<T>_BULLDIST`,
`#MFR_VIX_LO`, `#MFR_VIX_HI`, `HYG` (no HURST/TRENDLVL — those live in MFR_CORE_ALL).

## VIX proxy note (SUPERSEDED in the bot)
`$VIX` is unreachable via request.history, so the scripts proxy the VIX level with
`(#MFR_VIX_LO + #MFR_VIX_HI)/2` ("mid/close ~0.99, same Hedgeye bucket 87% of days
per TVC_VIX_1D check"). The bot uses the ^VIX index close itself (px_daily) —
operator instruction in the Round-2 Phase A brief.

## Context features (identical formulas in both scripts)
```
vix_level  = clamp(VIX, 5, 90)
vix_bucket = VIX < 20 ? 0 : VIX <= 30 ? 1 : 2      // Hedgeye 10-19 / 20-30 / >30
vix_roc5   = clamp(VIX/VIX[5 bars ago] - 1, -0.6, 1.5)
hyg_roc10  = clamp(HYG/HYG[10 bars ago] - 1, -0.15, 0.15)
```

## Clamp differences
| feature   | MFR_SPY_CORE_v3 | MFR_USO_CORE (v3) | MFR_CORE_ALL |
|-----------|-----------------|-------------------|--------------|
| bulldist  | +/-0.3          | +/-0.5            | +/-0.5       |
| hi_d3/lo_d3 | +/-0.1        | +/-0.2            | +/-0.2       |

The Python port uses the MFR_CORE_ALL / USO values (bulldist +/-0.5, hi/lo_d3
+/-0.2) for every ticker — the pooled-model spec, and what the Phase A brief
states ("bulldist (clamp +/-0.5)").

## decel encoding
The JS paints `((down3 && !decelerating) ? -1 : min(streak,7)) / 7` as ONE series.
The bot keeps `decel_streak` (0..7, from tools.volume_signal.decel_streak) and
`distribution` (0/1: price_down_3d AND NOT decelerating) as SEPARATE features per
the brief ("reuse volume_signal.py directly, don't re-port").
