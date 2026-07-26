# shadow_range.py — Integration Notes

## What this is
A self-contained shadow implementation of the MFR-style range feed:
Hurst exponent (R/S), realized vol (EWMA + Parkinson blend), a vol-adjusted
Hurst-skewed probable range, rp, trend state, and momentum — from OHLC alone.
Plus a calibration harness (fit params against archived MFR/hdg ranges) and a
live-feed validator (flag MFR values that diverge from the shadow).

Deps: numpy + pandas only. `python3 shadow_range.py` runs the offline
self-test suite (all passing as delivered).

## Integration order (do these in sequence)

1. **Wire prices in.** Feed each name's daily OHLC DataFrame (yfinance,
   oldest-first, >=130 rows preferred) to `compute_range(df, ticker)`.
   Output is a `RangeSnapshot`: price, low, high, rp, hurst, sigma, trend,
   momentum. Do NOT publish these numbers yet.

2. **Calibrate before trusting.** Build the reference set from the archive:
   rows of `[ticker, date, ref_low, ref_high]` using KNOWN-GOOD MFR ranges
   (pre-bug dates) and/or Hedgeye published ranges on the overlap tickers.
   Run `calibrate(price_data, reference)` — coarse grid first, then a tight
   grid around the winner. Persist the winning `RangeParams`.
   Sanity targets: band-edge MAE under ~1.5% of price, rp MAE under ~0.10,
   next-day close coverage in the high-80s/low-90s (98% on defaults = too
   wide; calibration should tighten k_width/horizon).

3. **Run as validator first, not replacement.** Each session, for every name,
   call `validate_feed(shadow_snapshot, mfr_low, mfr_high)`. Surface flags in
   the REPORT (e.g. a `!mfr` tag on the row). Two weeks of parallel running
   tells you exactly where MFR is rotten.

4. **Failover.** Where MFR is flagged or dark (the 11 dark names), publish the
   shadow range with `src=shadow` in the range-source column (alongside the
   existing mfr/hdg tags) so the desk always knows which authority set the
   level. hdg still overwrites everything, per hierarchy.

## Known limitations
- **No iv/ivpd.** Implied vol needs an options feed (SpotGamma Equity Hub CSV
  covers ~280 names; ORATS for full universe). `RangeSnapshot.iv` is the hook.
- **No volume tags.** Separate workstream (volume pipe).
- Hurst needs ~126 bars to stabilize; short-history names fall back to
  neutral skew (H treated as 0.5) — snapshot still valid, just symmetric.
- Futures/FX: works on any OHLC series, but calibrate asset classes
  separately — equity-fit params will not transfer to ZC_F/ZW_F.
- Defaults in `RangeParams` are priors, not answers. Everything downstream
  (gates, rp thresholds, screens) assumes calibrated ranges.
