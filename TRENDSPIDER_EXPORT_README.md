# TrendSpider custom-symbol export — reader's notes

Job: `trendspider_export.py` (daily 17:45 ET via `trendspider_export_launcher.ps1`;
`--dry-run --backfill` writes per-symbol CSVs to `ts_export/` with no DB writes).
Corpus staging: `ts_export_log`, deduped on (symbol, source_key) — an uploaded
row never re-exports. One symbol per POST, compact JSON separators (the default
`json.dumps` separators 504 the endpoint), 20s spacing.

## Symbol families

- `#MFR_<T>_LO/HI/TREND/RP` (SPY/UUP/VIX/USO/AAAU): TradingView MFR indicator
  history (2018→) spliced with the live feed at each ticker's first live date.
  known_at: bar_date 09:30 ET historical (values publish the prior evening),
  the stored row's write time live. RP = (prior session close − range_low) /
  (range_high − range_low), clamped [−0.5, 1.5]; prior close from TV bars
  historically, `eod_close_store` (unadjusted, job-maintained) live.
- `#MFR_<T>_LTLO/LTHI/LTRP/BULL/BEAR/BULLDIST`: TV history only (the feed's
  ltRangeData low drifts ~0.12% off TV; bull/bear levels are not in the feed).
- `#SHADOW_<T>_HURST`: the bot's own R/S Hurst (`shadow_range.hurst_rs`,
  126-bar), TV-close history spliced with `shadow_snapshots` live. Distinct
  from BOTH the feed's `hurst` (`#MFR_SPY_HURST`, live-only, unreproducible)
  and the indicator's `#MFR_<T>_HURST64/256` — three Hurst definitions by
  operator decision; the model sorts out which carries signal.
- `#CORR_*`: Macro Show USD correlation set (pearson on daily returns, the
  live `tools.relative_strength` function; TV closes historical).
- Round-2 full-indicator features (`#MFR_<T>_HURST64/HURST256/TRENDLVL/
  TRADELVL/BUY/MEGABUY/SELL/MEGASELL/VOLAT/VIXFIX/UPT1/DNT1/TRADE2/TREND2`,
  T ∈ {SPY, UUP, USO, AAAU, TLT}): loaded by `tradingview_ingest_full.py`
  from the 45-column exports; known_at bar_date 09:30 ET; TV-only.

## Caveats a later reader must know

- **TLT ranges are indicator-derived and UNVERIFIED against the live feed**
  (operator waiver 2026-09-06): the feed's TLT range diverges from the TV
  indicator on 53/81 overlap dates (0.07–0.29%), so every `#MFR_TLT_*` range
  symbol is TV-sourced end to end — history and live period, no feed union —
  keeping ML train and live on one definition. Rows carry
  `source='tv_indicator', feed_verified=false` in `ts_export_log`. The
  trading desk (REPORT/SCREEN) keeps the feed as TLT's source of truth.
- **`TRADE2` / `TREND2` are NOT duration counters.** Non-integer, per-bar
  series. Step-0 finding (2026-09-06): TRADE2 tracks the indicator's
  Volatility output at r ≈ +0.75…+0.81 on every ticker; TREND2 correlates
  weakly with everything tested (max r ≈ 0.39). Exact formulas unknown —
  they ship as opaque features; do not interpret them as counters.
- **`VIXFIX` is negative by construction** as exported ([−347, 0]); the sign
  is left exactly as the indicator plots it.
- **Values are always plain decimal** — TrendSpider's ingest silently drops a
  symbol whose CSV contains scientific notation (`8e-05`), returning HTTP 200
  regardless. `_fmt_value` guards this.
- Feed `hurst`/`hurst_3mo` ≠ indicator `HURST64`/`HURST256` (median gaps
  0.03–0.14, no lag alignment) — a third, feed-internal computation.
