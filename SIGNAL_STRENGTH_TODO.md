# Signal Strength — queued follow-ups

Tonight's commit (`35e96e2`) wired Keith's weekly Signal Longs/Shorts
into source flags, closing the biggest immediate gap (24 explicit
long/short tickers from a fresh weekly product that had been invisible
to the bot). This file tracks the remaining SS-family gaps for future
sessions.

## Background

See `outputs/signal_strength_pipeline.md` for the full state of the
five SS-family publications. Two of the five (daily SS Stocks + Position
Monitors / Founder's Choice) carry their actionable data in PNG images
that the text parser cannot read.

## Priority 1 — Daily SS Stocks PNG → Claude Vision OCR

**Context.** The "Signal Strength Stocks: 85 Stocks (M Added, K Removed)"
daily email body is 1,052 characters of plain text and lists only the
Add/Remove deltas. The full 85-stock ranked list lives in
`ss_<date>_update.png` with no alt-text fallback.

**Current workaround.** `parser_signal_strength` captures Add/Remove
deltas; the accumulated-membership query in
`tools/active_slice._fetch_db_sources` rebuilds a 127-ticker "currently
on SS" bucket from those deltas. Good but not perfect — tickers that
were on SS pre-window with no recent Add/Remove won't appear.

**Proposed fix.**

1. In `parser_signal_strength.process_email`, after the body is parsed,
   fetch the `<img>` URL pointing at `ss_*_update.png` and pass it
   alongside a structured-extraction prompt to Anthropic's Vision API.
2. Expected response shape: JSON list of `{rank, ticker, side?}` rows.
3. Write each to `hedgeye_signal_strength` with a new column
   `from_image=true` to distinguish from the delta path; set
   `is_added_today=true` only for tickers that are also in the email's
   "Added:" text.
4. Backfill the last 30 days of SS emails through the new path.

**Effort.** ~2-3 hours implementation + test. ~$0.001 per parse with
Claude Sonnet vision. ~$0.40/year ongoing.

**Why not tonight.** Net-new external dependency, real API-cost
discussion warranted, and the operator-paste path (`config/ss_full_list.yaml`)
+ tonight's Keith Signal Longs/Shorts wiring covers the urgent gap.

## Priority 2 — Position Monitors / Founder's Choice sector tables

**Context.** 33 sector-specific emails over the last 3 months covering
Industrials, Energy, Global Technology, Retail, Communications,
Financials, Gaming, Healthcare, Software, Consumer Staples,
Restaurants. Each carries a "BEST IDEAS - LONGS" / "BEST IDEAS -
SHORTS" image table. Currently `parser_position_monitors` records
publication events only (no ticker extraction).

**Proposed fix.** Same Claude Vision pattern as Priority 1 — pass the
image to Vision and structure-extract per-sector long/short lists. Write
to a new typed table `hedgeye_position_monitor_picks` keyed on
`(snapshot_date, sector, ticker, side)`.

**Effort.** ~3-4 hours including new table migration + per-sector
bucket population in `tools/active_slice`.

**Why not tonight.** Sequence after Priority 1 — both share the same
Vision pipeline. Build the pipeline once, apply twice.

## Priority 3 — `signal_strength_long` / `_short` split for the
   daily delta-based bucket

**Context.** Tonight's commit added explicit-side buckets only for the
weekly Keith's L/S product. The daily SS Stocks bucket
(`signal_strength`, 124 tickers via accumulated-membership) is still
implicit-long under `_ticker_side` Tier 2 because the deltas don't
carry side info.

**Proposed fix.** Either (a) extend `parser_signal_strength` to
distinguish "Best Idea Longs" vs "Best Idea Shorts" sections in the
Add/Remove text (if the text body actually mentions which section a
ticker was added to — needs raw-email audit), or (b) infer side from
Vision OCR (depends on Priority 1).

**Effort.** ~1 hour for (a) if the section info is in the email text;
otherwise (b) is the path.

## Priority 4 — Operator MFR watchlist gap

**Context.** Not a code problem. 87 of the 127 daily-SS tickers (and
some/all of tonight's 24 Keith's L/S tickers) are not in operator's MFR
watchlist. Without MFR range data, the bot's gate short-circuits to
WATCH/Monitor and the alert never reaches Telegram.

**Proposed fix.** Operator pastes the list from
`outputs/ss_to_add_to_mfr.txt` (87 tickers, all real US equities, no
FX/macro junk) into the MFR portal.

**Effort.** 5 minutes of operator UI work.

## Priority 5 — Hedgeye dashboard scrape

**Context.** `scraper.py` exists but is dormant — reCAPTCHA blocks
reliable headless login to `app.hedgeye.com`. If a clean Vision-based
approach to Priority 1+2 works, the scraper path can stay dormant.

**Not recommended.** Defer indefinitely unless Vision OCR proves
insufficient.

---

## Decision matrix for next session

| If operator wants to … | Do this |
|---|---|
| Cover the full daily 85-stock SS list automatically | Priority 1 (Vision OCR) |
| Cover sector-level Long/Short picks across 11+ sectors | Priority 2 (Vision OCR) |
| Just get more SS-family alerts firing today | Priority 4 (MFR watchlist paste) |
| Eventually replace email parsing with a robust scrape | Priority 5 (revisit only if Vision fails) |
