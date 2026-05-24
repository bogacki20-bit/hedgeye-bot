# Notifier-rollback audit — 2026-05-24

Snapshot of the current architecture before the strip. Captures every touch
point the rollback plan needs to flip.

## 1. Decision path (live)

`proactive_scanner.py` (CLI / scheduled) → `_resolve_watchlist()`
→ per-ticker `unified_refresh.refresh_all_for_tickers([ticker])` (MFR + SG queue + Yahoo)
→ `decision_engine.decide(ticker)` (Claude API)
→ `_persist_recommendation()` → `_send_alert()` (Telegram).

- Scheduler entry: `scanner_launcher.ps1`. Currently runs
  `proactive_scanner.py --source hedgeye_active --lookback-days 7 --workers 16
  --throttle 0` once per Task Scheduler trigger. **No outer loop, no cadence
  in the script itself.**
- Active universe = `tools.list_monitored_tickers.fetch_hedgeye_active(7)` —
  UNION of every ticker in `hedgeye_risk_ranges`, `hedgeye_signal_strength`,
  `hedgeye_portfolio_solutions`, `hedgeye_etf_pro_ranges`,
  `hedgeye_investing_ideas` within the last 7 days. **Not Quad-aware.**

## 2. Quad inputs

Env-var path already exists and short-circuits everything:
- `tools/doctrine.py::current_quarterly_quad()` checks
  `CURRENT_QUARTERLY_QUAD_OVERRIDE` first, then `bot_state`, then default
  `Quad 1`. Same for monthly. ✅ canonical input is already wired.
- `tools/detect_quads.py` writes `bot_state` from Macro Show dashboards.
  Has the env-override short-circuit at the top of `run()` (lines 156–161)
  so the autodetect already skips when env is set.
- Scheduled by `quad_detector_launcher.ps1`.

**Action:** make `tools/detect_quads.py` a no-op when env vars are set (it
already is); document the env-var path as canonical; leave the launcher in
place but make it report-only (don't write `bot_state`).

## 3. SpotGamma — live-path touch points

These get stripped (read paths). Ingestion / write paths stay.

| File | Lines | What | Action |
|---|---|---|---|
| `decision_engine.py` | 184–191 | `_get_spotgamma_latest()` fetcher | Remove from `gather_context` |
| `decision_engine.py` | 238–291 | `_get_flowpatrol_for_ticker()` | Remove call in `_format_user_message` |
| `decision_engine.py` | 330 | `"spotgamma": _get_spotgamma_latest(...)` | Drop |
| `decision_engine.py` | 504, 512–565 | `_spotgamma_framing_line()` | Dead with new prompt — leave defined, unused |
| `decision_engine.py` | 731–746 | SG in `_cross_source_eval` | Dead with new prompt |
| `decision_engine.py` | 977–979 | `_format_user_message` SG section | Removed in new prompt |
| `decision_engine.py` | 1023–1124 | SG citation validator + flagger | Removed in new prompt |
| `decision_engine.py` | 1339–1340 | `context_summary.spotgamma_*` | Drop |
| `monitor_context.py` | 177–361 | `get_spotgamma_ctx`, `_parse_spotgamma_fields`, `_fetch_typed_spotgamma` | Leave defined; stop calling from live path |
| `price_monitor.py` | 130–140 | `MACRO_NO_SG_TICKERS` | Dead (no SG reads) — leave |
| `price_monitor.py` | 300–448 | `_sg_levels_suffix`, `_cross_source_suffix` (consumes SG) | Strip from `compose_recommendation` |
| `price_monitor.py` | 491–639 | `compose_recommendation` plumbing `spotgamma_context` | Pass `{}` everywhere |
| `price_monitor.py` | 808–830 | `db_pg.record_alert(... spotgamma_context=rec.get(...))` | Pass `None` |
| `proactive_scanner.py` | 231 | Alert template: `sg put/call walls` | Replace with the new 1-line template |
| `proactive_scanner.py` | 306 | `spotgamma_reason="proactive_scan"` | Keep — still triggers SG snapshot ingestion |
| `db_pg.py` | 462, 486, 502, 513, 528, 551, 578 | `spotgamma_context` JSONB column on `alerts_fired` | Column stays (ML pipeline); writes pass None |
| `unified_refresh.py` | 87–104 | SG leg in refresh fan-out | **Keep** — this is the ingestion path |

**KEEP intact (SG ingestion, not live read):**
- `spotgamma_client.py` (queue + scrape + `spotgamma_snapshots` writes)
- `corpus_ingest.py::discover_spotgamma`
- `ingest_sg_course.py`
- `data/snapshots/spotgamma/**` (raw md captures)
- The `spotgamma_snapshots` table and `alerts_fired.spotgamma_context` column
  (write None going forward; reads dropped)

## 4. Decision-engine prompt structure (to collapse)

Current 3-block cached content array:
1. **static_framework** (1h TTL cache): framework_canon.md + Hedgeye doctrine summary
2. **per_ticker_static** (1h TTL cache): RR + ETF Pro + SG + MFR + macro + Quad + corpus snippets
3. **dynamic** (uncached): ticker, account, live Yahoo, recent alerts

System prompt: ~510-line GIP doctrine block.
Model: `DECISION_ENGINE_MODEL` env, default `claude-sonnet-4-5`.
Max tokens: 1500. Strict JSON output. Post-hoc validators flag SG-not-cited
and breach-framing contradictions.

**Collapse target:** single ~10-line user prompt to Haiku 4.5, no system
prompt cache, plain text 1-line BUY/WATCH/SKIP output.

## 5. yfinance call sites

- `price_monitor.fetch_prices()` (line 219) — batch `yf.download`
- `yfinance_client.fetch_raw()` (line 70) — per-ticker `yf.Ticker.history`
- `tools/correlation_tracker.py:69` — `yf.Ticker.history` (correlation calc)

All three go behind `PRICE_FEED=yfinance|polygon` flag. Polygon stub: only
`yfinance_client.fetch_raw` needs a replacement at first (it's the only
single-quote path); the batch + correlation paths stay on yfinance unless/
until POLYGON_API_KEY lands.

## 6. MFR universe discovery

There is no static "MFR-300" list in code — MFR is a per-ticker API endpoint.
The de-facto universe = whatever has landed in any of the Hedgeye product
tables (the `hedgeye_active` resolver). For the rollback I'll bootstrap the
mfr_quad_map.yaml from:

1. `config/hedgeye_doctrine.yaml::quad_universe` (~33 unique tickers across
   all 4 Quads) — `seed=doctrine`
2. Recent `hedgeye_active` universe from the Railway DB (last 7d UNION) plus
   `hedgeye_ticker_inventory` (active rows) — `inferred=claude` for any
   ticker not in the doctrine seed
3. `parser_*` files reference a long tail of single-stock tickers (XOP, OIH,
   MSFT, AAPL, AMZN, META, GOOGL, NFLX, TSLA, NVDA, ORCL, KRE, AAAU, ALLW,
   ...) and price_monitor's `HEDGEYE_TO_YFINANCE` map (~50 entries) — fold
   these in too.

## 7. Tier-1 Alpha

`grep -ri "tier1_alpha\|t1a\|T1A" .` (excluding worktrees) → **no matches**.
Greenfield. Drop a `parser_t1a.py` stub + TIER1_ALPHA_TODO.md.

## 8. DO NOT TOUCH (per spec)

- `tools/compute_outcomes.py`
- `actions_log`, `outcomes_log` schemas
- `alerts_fired` schema (column stays even if write-only None)
- Anything under `migrations/`
- Email parser pipeline (`email_parser.py`, all `parser_*.py`)
- `mfr_client.py`, `spotgamma_client.py`, `yfinance_client.py` ingestion
- `data/snapshots/**` raw captures
