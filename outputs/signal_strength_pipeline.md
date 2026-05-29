# Signal Strength capture pipeline — diagnostic + state (2026-05-29)

Hedgeye's "Signal Strength" is **five distinct publications** under one
umbrella. The operator was tracking the gap correctly all evening; the
core issue was that one of the five products (the one with the most
actionable text-based ticker data) wasn't wired into `tools/active_slice`.

## The five SS-family publications

| # | Sub-product | Email subject pattern | Cadence | Parser | Table | Data quality |
|---|---|---|---|---|---|---|
| 1 | **Daily SS Stocks update** | `Signal Strength Stocks: N Stocks (M Added, K Removed)` | Daily | `parser_signal_strength.py` | `hedgeye_signal_strength` | Add/Remove deltas only — full ~85-stock list is a PNG image, invisible to the text parser |
| 2 | **Keith's Signal Longs/Shorts** | `Keith's Signal Longs/Shorts` | Weekly (Sunday/Monday) | `parser_keiths_signals.py` | `hedgeye_keiths_signals` | **FULL ticker list, explicit side** (text body) |
| 3 | Position Monitors (sector) | `Position Monitors \| <sector> - Best Idea Longs & Shorts` | Per-sector ad-hoc | `parser_position_monitors.py` | `hedgeye_position_monitors` | Publication log only — long/short tables are images |
| 4 | Founder's Choice (sector) | `Founder's Choice: <sector> - Best Idea Longs & Shorts` | Per-sector ad-hoc | Same parser | Same table | Same — image-only |
| 5 | Real-Time Alerts | `**Real-Time Alert: <analyst> Buy/Sell Signal (Signal Strength ...)` | Intraday on triggers | `parser_rta.py` | `hedgeye_rta` | Per-event with side+action |

## The gap that was closed in `35e96e2` (this commit)

`hedgeye_keiths_signals` carries **404 rows over 15 weekly snapshots** with
explicit `(ticker, side)` per row. Latest snapshot 2026-05-26 (3 days
old, fresh under the 7-day weekly cadence) has **5 LONGS + 19 SHORTS**:

```
LONGS  (5):  AFRM, CACC, CPAY, FCFS, V
SHORTS (19): ADYEY, AXP, COF, EFX, EXPN, FISV, FOUR, MA, MCO, OMF, OPEN,
             PYPL, RKT, SOFI, SPGI, SYF, TRU, WFC, ZG
```

This is the **FINANCIALS-sector Signal Strength** publication. Before
this commit, `tools/active_slice` never queried this table — all 24
tickers were invisible to side resolution. SOFI / MA / AXP / etc. were
not in any source bucket, despite being on Keith's current Short list.

### What this commit does

1. **Adds two new buckets** to `source_breakdown()`:
   - `signal_strength_long`  — pulled from `hedgeye_keiths_signals` latest snapshot, side='long'
   - `signal_strength_short` — same query, side='short'
2. **Includes both in `_POLLING_INCLUDED_KEYS`** — so these tickers
   join the polling universe.
3. **Adds new staleness key** `signal_strength_ks` → 7 days (weekly
   product, not 3-day daily).
4. **Updates `_ticker_side` Tier 1** to recognize `signal_strength_long`
   and `signal_strength_short` alongside `etf_pro_long` / `etf_pro_short`
   as explicit-side flags. Conflict detection works the same way.
5. **Updates `_compute_source_label` cascade** with `[Signal Strength
   Short]` / `[Signal Strength Long]` ranked above the implicit
   `[Signal Strength]` label (because the explicit-side flags are
   more specific).

### Hermetic verification

```
SOFI   side=short  label=[Signal Strength Short]  flags=['signal_strength_short']
                   top_edge bearish/bearish → SHORT
                   "top-edge short entry — bearish trend + bearish momentum"

V      side=long   label=[Signal Strength Long]   flags=['signal_strength','signal_strength_long']
                   bottom_edge bullish/bullish → BUY
                   "bottom-edge scale-in — bullish trend + positive momentum"

MA, AXP, SPGI, COF, PYPL — all side=short, label=[Signal Strength Short]
AFRM, CACC, FCFS — all side=long, label=[Signal Strength Long]
```

### Production impact

- Polling universe expanded from 247 → 263 tickers (+16 net, after
  dedup with existing buckets)
- Side-aware verbs (SHORT / COVER / WATCH-AVOID) will now fire on
  SOFI / MA / AXP / etc. when they hit edges
- Source label cascade gives `[Signal Strength Short]` instead of the
  generic `[Quad]` or `[Signal Strength]` it would have rendered before

## What this commit does NOT fix (queued follow-ups)

See `SIGNAL_STRENGTH_TODO.md` for the prioritized follow-ups.

Summary:

- **Option 2 — Claude Vision OCR on the daily SS Stocks PNG**: would
  give the full 85-stock list from the daily product (not just the
  Add/Remove deltas + the weekly Keith's L/S financials). Real but
  small API cost.
- **Option 3 — Hedgeye dashboard scrape**: complex, fragile.
  Not recommended.
- **Option 4 — `config/ss_full_list.yaml`** (shipped earlier): operator
  manually pastes the SS list. Already available as a fallback.

## What this commit did NOT address from operator's earlier asks

- **MFR coverage gap**: 87 SS tickers still missing from operator's
  MFR watchlist (see `outputs/ss_to_add_to_mfr.txt`). Operator action
  on the MFR portal.
- **Quad env-var setting on Railway**: still required for full Quad
  bucket population.
