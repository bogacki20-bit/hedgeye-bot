# ROUND 2 DATA ROADMAP — regime & positioning history for the bot's own ML (2026-09-06)

Round 1 verdict (see TRENDSPIDER_ML_ROUND1_RESULTS): price-derived MFR features alone don't beat the random band out of sample. The missing layer is regime + positioning history. This is how to get it, cheapest first.

## 1. Realized Quad — free, decades (Sean, ~1 day)
- Rule: Quad = sign of Δ(YoY real GDP growth) × sign of Δ(YoY CPI) vs prior quarter. Q1 G↑I↓ · Q2 G↑I↑ · Q3 G↓I↑ · Q4 G↓I↓.
- Quarterly: FRED GDPC1 (real GDP), CPIAUCSL. Cross-check against the historical Quad table in MASTER_THE_MARKET (1Q98→) in Context — it is Hedgeye's own print; where ours disagrees, theirs wins and we note the rule difference.
- Monthly nowcast version: same rule on the monthly inputs Hedgeye uses (INDPRO, RSAFS, PAYEMS, CPI, PPI, PCE) — YoY, then 2nd-derivative vs prior month. Store as `quad_realized(month, quad, g_roc, i_roc)`.
- known_at = data release date + 1 day (BEA/BLS calendars) — NOT the month it describes. No lookahead.

## 2. Hedgeye's stated view — back-parse Kris's own archive (Sean, 1–2 wks)
- Source: Kris's subscriber archive (Early Look, Macro Show slides, The Call, Re-Rank). Personal use only; never redistributed.
- Parse per dated note: stated current Quad (monthly/quarterly), forward Quad path, bullish/bearish/neutral list, Top-5 Most Actionable, risk ranges per instrument (low/high/tag), VIX bucket call, "rate of change" phrases (getting less bearish, etc.).
- Table `hedgeye_view_history(note_date, product, field, value, known_at = publish time ET)`. ~4 yrs ≈ 1,000 daily rows.
- Start with Early Look risk ranges + Quad call; everything else is Round 3.

## 3. Vol-regime proxies — free from CBOE, 2006–2011 → now (Sean, ~½ day)
- VIX, VIX3M (term structure = VIX/VIX3M), VVIX, SKEW, COR1M, COR3M. Daily CSVs on cboe.com.
- Not gamma, but the same information family at daily resolution, and they cover the whole 2019–24 training window. Features: level, bucket, 5d ROC, term-structure ratio, COR1M<8 flag.

## 4. SpotGamma + Tier1Alpha history — ask the vendors (Kris, one email each)
- Request: historical daily export of the key levels since account inception (SG: Vol Trigger/Zero Gamma, Call Wall, Put Wall, Absolute Gamma, net gamma sign, COR1M, 25Δ RR, HIRO EOD; T1A: systematic flows, gamma flip, next catalyst). CSV is fine.
- If sold/provided → `sg_history` / `t1a_history` with known_at = their publish time (SG AM ≈ 05:30 ET; T1A morning).
- Fallback if refused: reconstruct dealer gamma from historical option OI (CBOE DataShop / OptionMetrics — paid, real project). Do NOT start here.

## 5. Daily capture going forward (Sean, now)
- Repair the SG canary (dead since 2026-08-06) and add a T1A capture (currently morning pastes only). Every day not captured is a training row lost.

## 6. Model (after 1–3 land)
- In Python on the bot's corpus (LightGBM / sklearn), NOT TrendSpider. Walk-forward validation split by regime, honest known_at joins.
- Target reframed: given a setup the rules already allow (PUCK + real dip + uncrowded), rank forward 20/30-day risk-adjusted return — a ranker/filter, not a signal generator.
- Features: MFR state + ROC (v3 set), Hurst64/256, distance to TREND/TRADE levels, quad_realized, hedgeye stated/forward quad, vol proxies, cross-asset (uup_rp, corr30s, usd_pressure), later SG/T1A.

## Order
1 → 3 → 4 (emails out now) → 5 → 2 → 6. TrendSpider's role after 9/18: charting + MFR visibility on all assets; the pooled multi-asset model is the last ML test there.
