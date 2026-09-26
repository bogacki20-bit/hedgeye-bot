# Macro Show slide deck daily capture

**What:** the whole Macro Show deck → `tms_decks` (migration 115), one row per
show date. The quad model's estimate quarters also go to `hedgeye_quad_stated`
(product `Macro Show Deck`, scope `quarterly`). Summary: `py parser_tms_deck.py --show`.

**Where it comes from:** the ~08:30 "THE MACRO SHOW: <date> - Access Show,
Slide Deck & Top 3 Things" email → "CLICK HERE TO ACCESS TODAY'S SLIDES" →
`app.hedgeye.com/mu/tms_<hosts>_<m-d-yyyy>` → signed S3 PDF (5-minute expiry).
The deck goes up after the 9:00 show, so the task runs at 09:45 ET. Same
Chrome + curl path as the MSR (docs/T1A_CAPTURE.md): it needs Kris's signed-in
Chrome and home IP.

## What's in a deck (order fixed, lengths vary)
| Section | How it's captured |
|---|---|
| Top 3 Things | text layer (no vision): topic + Keith's commentary |
| Our Process | text of every page, hashed. **"What Are The Quads?"** (has "Updated:") is the live GIP model and gets read in full: GDP/CPI actuals + estimates and the quad path. Any other process page whose text changes vs the last deck is rendered and flagged |
| Chart Callouts | changes daily (9–24 pages): title, source, printed levels, one-line summary per page |
| Macro Grind | sector perf / relative, volume, style factors, $USD correlations, CFTC, series boxes (yield curve, GS momentum/retail) |
| Volatility | implied vs realized vol (~41 tickers), macro vol boxes, series boxes (MOVE, HY OAS, FX vol, BBB spread) |
| Hedgeye Events | skipped |

Tables are images: the Macro Grind and Volatility pages show the **prior close** (`data_date`).

## Daily path (scheduled task `tms-deck-capture`, weekdays 09:45 ET)
1. `py parser_tms_deck.py --link` → the `/mu/` URL (exit 1 = email not in).
2. Open it in Kris's Chrome; read the final hedgeye.s3.amazonaws.com URL.
3. `curl -s -o data/snapshots/hedgeye/<D>/macro_show/tms_deck.pdf '<s3 url>'`; check `%PDF`.
4. `py parser_tms_deck.py --prep <pdf> --date <D>`: splits, captures text,
   renders `tms_pNN.png` for each page to read, writes the skeleton
   `tms_deck.json` (its `_pages` list says which key each page fills;
   `_instructions` is the schema). `--zoom <pdf> <page>` renders one page at 3x.
5. Read the PNGs and fill every null key. Chart-line reads go in `approx_fields`.
6. `py parser_tms_deck.py --json <json> --date <D>`: validates and stores.
   The validator recomputes each quad from the GIP table (sign of the change
   in GDP YoY and CPI YoY) and blocks a path that disagrees. It also checks
   sector tickers and row counts, CFTC z-scores, and VIX range.
7. `py parser_tms_deck.py --send --date <D>` → the morning Telegram card
   (~9:50, own message, lands after the 9:40 desk note). The laptop .env has
   no Telegram token; `--send` loads it in-process from `railway variables`.
8. `py parser_tms_deck.py --show --date <D>` → report the block.

**EOD pack:** `tools/eod_stat_pack.py` appends the MACRO SHOW DECK block
(`py parser_tms_deck.py --eod` previews it): the latest deck on or before the
pack's session, header-labeled with the close its tables use, plus CFTC
extremes, factor leaders, cheapest/richest IVol and every callout summary. The
pack on Railway only shows it once `parser_tms_deck.py` is deployed.

## Quads
The deck **states** Hedgeye's quarterly quad nowcast. It never writes
`quad_regime_history`: `QUAD:` stays the only input for the confirmed regime
(tools/quad_regime.py). `--show` prints the deck's current-quarter quad next to
the confirmed one and flags a mismatch or a newly revised model. The monthly
quad isn't on the deck's process pages (checked 7/24, 9/24, 9/25).

Do NOT: sign in, grant browser permission prompts, or retry more than once.
