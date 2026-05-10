---
name: spotgamma-refresh-queue
description: Drain the SpotGamma refresh queue file — for each ticker listed, scrape the equity hub via Chrome MCP, save the markdown, then ingest + populate spotgamma_snapshots. Designed to be invoked manually or scheduled (e.g. every 30 min during market hours).
---

This skill drains `C:\Projects\hedgeye-bot\.commands\spotgamma_refresh_queue.json` and produces an on-demand SpotGamma capture for each ticker listed there. The Python side (`spotgamma_client.py`) writes to that queue when it sees stale data; this SKILL is what actually fetches the fresh data via Chrome MCP.

## Pre-flight

1. Read `C:\Projects\hedgeye-bot\.commands\spotgamma_refresh_queue.json`. If the file does not exist or `tickers` is empty, write `data/snapshots/spotgamma/{YYYY-MM-DD}/_refresh_queue_empty.md` with the timestamp and stop.
2. If Claude in Chrome reports no connected browsers, write `data/snapshots/spotgamma/{YYYY-MM-DD}/_refresh_FAILED_no_browser.md` with the timestamp and the queue contents, then stop.
3. Get today's date in EDT (run `date +%Y-%m-%d` via mcp__workspace__bash if unsure).

## Per-ticker capture loop

For each TICKER in the queue:

1. `mcp__Claude_in_Chrome__navigate` to `https://dashboard.spotgamma.com/equityhub?sym={TICKER}&eh-model=legacy`
2. `wait 3 seconds` for the right panel to render
3. `mcp__Claude_in_Chrome__get_page_text` and extract the same fields the daily sweeps capture: Current Price, Daily Change, Previous Close, Earnings Date, Stock Volume, 52-Week High/Low, Call Wall, Put Wall, Hedge Wall, Key Gamma Strike, Key Delta Strike, Call Gamma, Put Gamma, Top Gamma Exp, Top Delta Exp, Largest Gamma/Delta Strike, Gamma Hedge Est, Call/Put Volume, Put/Call OI Ratio, 1 M RV, 1 M IV, IV Rank, Skew Rank, Garch Rank, Options Implied Move.
4. Write `data/snapshots/spotgamma/{YYYY-MM-DD}/equityhub_ondemand/{TICKER}.md` matching the format in `data/snapshots/spotgamma/2026-05-08/equityhub_eod/OIH.md` — same headers, same tables. The header line should say `# SpotGamma {TICKER} Equity Hub — {YYYY-MM-DD} on-demand`. Include a "Reason" line in the metadata block citing the queue's `reason` field.
5. If the navigate or get_page_text fails, append `{TICKER}: ERROR — {reason}` to `_refresh_run_log.md` and continue with the next ticker; don't abort the loop.

## After the loop — ingest and populate

After all tickers are captured, queue two bridge commands in sequence:

1. `python_script` running `corpus_ingest.py` with no args — pushes the new markdown files into `corpus_documents` (idempotent, skips dupes).
2. `python_script` running `spotgamma_client.py` with args `["--populate"]` — walks the snapshots tree and upserts typed rows into `spotgamma_snapshots`, including the new on-demand captures.

Wait for both result files in `.commands/results/` and confirm `failed=0` in each. If either reports failures, append the error to `_refresh_run_log.md`.

## Drain the queue and ping

Once both ingest steps succeed:

1. Queue a `python_script` bridge command running `spotgamma_client.py --queue-status` to confirm the queue state, then a one-shot script that calls `spotgamma_client.read_refresh_queue(drain=True)` (or simply `del C:\Projects\hedgeye-bot\.commands\spotgamma_refresh_queue.json` via the bridge — equivalent, simpler).

Actually simplest: write a helper python_script `drain_spotgamma_queue.py` that wraps `spotgamma_client.read_refresh_queue(drain=True)` and add it to the bridge ALLOWED set. Or invoke via existing `spotgamma_client.py` CLI by adding a `--drain` flag.

2. Append a final summary section to `data/snapshots/spotgamma/{YYYY-MM-DD}/_refresh_run_log.md`: tickers attempted, succeeded, failed, total elapsed time, queue drained=true.

3. Send a Telegram ping via the bridge:
```json
{"command": "telegram_send", "args": {"text": "SpotGamma on-demand refresh complete. Tickers: {N}. Failures: {M}. Reason: {reason}."}}
```

That's it. Single run, then done. Do not commit or push.

## Integration notes

- **Trigger source**: today the queue gets populated by manual `spotgamma_client.py --queue --tickers ...` invocations. Slice 3 (`email_parser` hook) will populate it automatically when a Hedgeye email arrives mentioning tickers whose typed rows are stale.
- **Schedule cadence**: invoke manually as needed, or attach to a cron like `*/30 9-16 * * 1-5` (every 30 min during market hours) once trigger volume justifies it.
- **Bridge whitelist**: the bridge ALLOWED set already includes `corpus_ingest.py` and `spotgamma_client.py` (added in commits 8c7c175 / 346dc72), so the post-loop ingest steps will work without further bridge changes.
