# Hedgeye Bot — Telegram Command Reference

**Source of truth** for every chat command and alert type (printable card:
`COMMAND_CARD.html`). Generated from the actually-registered handlers in
`telegram_handler.py`. Dispatch order: **SS → QUAD → SCREEN → quad-confirm →
MOVES → MFR BACKLOG → SOURCES → WRAP → REPORT → trade-decision → echo**.
Only messages from your whitelisted chat are processed. Anything unrecognized
echoes back (`Got it: <text>`) — no side effects. A handler crash is surfaced
as `🛑 handler error`, never a silent echo.

---

## 🟢 Read-only commands (never write)

### `REPORT` — EOD fact sheet (v4)
- **Syntax:** `REPORT` (compact Telegram mode) · `REPORT NOW` (intraday —
  see below) · `REPORT FULL` (unfiltered ⚡DIV) · `REPORT UPLOAD` (verbose
  .txt attachment: full tgt/src per line, full DIV, position table
  appended — for pasting into an LLM) · `REPORT LEGACY` (v3 renderer,
  parallel-run week) · `BOOK FULL` (per-account position table as .txt —
  never inlined into chat)
- **Output, in order:** header (`REPORT v4 <date> [kind]` + legend) ·
  `Δ since last:` (new ⚠ · SS drops touching book · biggest sector rp move
  ≥0.10, vs the previous stored snapshot) · `QUAD:` + last confirm ·
  `VOL:` regime line (trend, range-pos, phase per sleeve) · `SECTOR FLOW`
  (rp + Δ3d, money-in first, **✓/✗ flow-quality**: ✓ = Δrp agrees with range
  structure, ✗ = fade-vs-structure — rising rp inside a descending range) ·
  `RANGE DYNAMICS` (HH/HL asc · LH/LL desc · HH/LL widening · LH/HL
  compressing) · `DOLLAR+BONDS` · `SS FLOW` churn · `BOOK:` exposure-frame
  counts (cash-equivalents excluded) + flagged names with **fill context**
  compact `⚠TLT(L,rp0.34,BEAR,7.3%acct,73%,IND,-2.1%pl)` — % of its
  account, fill % of target, account (or `RIRA+IND` aggregated on splits),
  unrealized P&L. The target itself (`→10.0%tgt·dflt-fi`) prints only when
  explicit or bucket=OVER (compact), always in UPLOAD mode. Default tiers
  (v4.1): fi 10 · core 4 (broad/theme ETF) · eq 2 (single name) · sat 1
  (inverse/levered, commodity, single-country, crypto, ALL shorts);
  explicit `TARGET` rows override. Buckets: <40% STARTER · 40-80 BUILDING ·
  80-110 FULL · >110 OVER · `CASH:` settled $ + parked (cash-equivalents,
  e.g. BUXX) = deployable, % of AUM +
  % of AUM (unsettled n/a — not in the Fidelity export) · `CONC:` top-3
  weight clusters from ticker_tags (sector / rate_sensitive / dur:*) +
  untagged bucket + top_cluster · `⚡DIV` scoped to book∪bench∪SS (full list
  via `REPORT FULL`) · `CANDIDATES:` rule-based nomination, rule printed
  inline (`TREND=BULL + rp<0.35 + fill<80%`) — no ranking, no advice ·
  `ALERTS:` today's alert *contents* (capped 8, then +N more).
- Facts only, no narrative — built to be pasted into an LLM for reasoning.
  Missing data prints `n/a` with a reason, never disappears. Stored to
  `report_rows` (nightly `eod` rows = ML corpus); each v4 build also stores
  a `report_snapshots` state row that powers the next Δ-header.
- *(handler: `tools/report.py → handle_report_command`)*

### `SCREEN` — natural-language screener
- **Syntax:** `SCREEN <plain english>` — **direction required** (`longs` /
  `shorts`; the Rule-1 TREND gate is tied to it).
- **Examples:** `SCREEN energy shorts top of range` · `SCREEN etf pro shorts`
  · `SCREEN my book shorts` · `SCREEN keiths longs near the bottom`

**Phrases (mix freely):**

| Phrase | Effect |
|--------|--------|
| `longs` / `shorts` | direction + TREND gate (BULLISH / BEARISH) — or POSITION side in book mode, see below |
| **source lens** — `etf pro`, `portfolio solutions`, `investing ideas`, `keith's signals`/`keiths`, `signal strength`, `position monitor`, `btc quant` | screen a whole signal source. No source = tagged roster. Sided sources (`keiths`; `btc quant`) filter on their stored side AND the TREND gate, rendered per row as `side:short` |
| any GICS sector — `healthcare`, `tech`, `energy`, `financials`, `staples`, `discretionary`, `industrials`, `materials`, `utilities`, `real estate`/`reits`, `communication(s)`, `digital assets`/`crypto` | sector filter |
| `bottom of range` / `near the low` | range_pos ≤ 0.20 |
| `top of range` / `near the high` | range_pos ≥ 0.80 |
| `above/in/below the cloud` (aliases: `above/below trend range`) | price vs the MFR trend range (yellow band); rendered `lt:above` / `lt:in=0.42` / `lt:below` |
| `momentum` / `with momentum` | require bullish MFR momentum |
| `in my book` / `that I own` / `held` | only names you hold (**book mode** when no source lens) |
| `show gated` / `show all` / `include gated` | list names Rule-1 dropped |

**BOOK MODE** (`SCREEN my book shorts` — held, no source lens):
- **Direction = POSITION side**, not trend. Sides are computed by Python from
  actual book legs: signed quantity, options net-of-spread (long put = short
  exposure, premium-weighted so spreads net correctly), **inverse wrappers
  linkage-adjusted** — SBIT held long counts as a SHORT (`book shorts` means
  exposure shorts). This is the SHY/TUA/HEFT/AGGH fix: a bearish-trend long
  is still a LONG.
- **TREND never drops a held row** — it renders per row; trend against your
  position gets **`⚠️trend-against`** (raw holding side vs linkage-adjusted
  trend — frame-invariant, so an inverse wrapper with an intact thesis is
  never falsely flagged).
- **`◻️ side-indeterminate`** — held but no long/short verdict (flat spread
  or unjudgeable legs; check ingest). Listed, never silently dropped.
- `side:` field shows the position side per row; wrappers show
  `u:META·BULL↯inv` (underlying, its trend, inverse marker).

**Per-row columns:** tier (`●●` active · `●` top-idea · `·` bench · `—`
untagged) · `trend·src` (hdg / mfr / btcq / undr=wrapper-underlying) · `rp` ·
`mom` · `h` Hurst · `iv rv ivpd` · `cSPY cUUP` (bot-computed, `?` if <20d).
Flags: `⚡DIV(...)` trade-vs-momentum divergence · `📗own` held ·
`⚠mfr-only` top-idea gated on MFR trend only · `⚠️trend-against` ·
`side:` · `u:…↯inv` · `lt:…`.

**Nothing disappears silently:** `🌑 DARK` (no MFR range) ·
`⛔ GATED BY TREND` (count, or full list with `show gated`) ·
`◻️ side-indeterminate` (book mode) · empty results name the first funnel
stage that hit 0.
- *(handler: `tools/screener.py → handle_screen_command`)*

### `REPORT NOW` — intraday companion
- **Syntax:** `REPORT NOW`
- **What it does:** LIVE prices (one batched yfinance call) against the
  STORED MFR ranges, over the FULL MFR universe with a defined signal (not
  just the SS list). Rotation cues from the same sector-flow Δ3d/✓✗ math
  the EOD prints: hot sectors → LONG candidates (trend BULL, live rp≤0.35),
  cooling sectors → SHORT candidates (trend BEAR, live rp≥0.65, IND-only —
  IRAs are long-only). ALL compliant names surface even at cap — breaches
  are flagged (`⚠cap4` ETF over 4% report cap · `⚠ceil6` over 6% ceiling ·
  `⚠HARD2` short over the hard 2%), never hidden. Sizing header: longs
  start 100 bps add 50/100, shorts start 50 bps. `EDGES` = book positions
  at live range extremes (rp≤0.15 / ≥0.85; unclamped — 1.2 means through
  the top). Coverage line counts no-price and untagged names (untagged are
  invisible to rotation cues — tag to include). Takes ~15-30s to reply
  (live price batch). Stored to `report_rows` kind=`now`. Read-only.
- *(handler: `tools/report_now.py` via `tools/report.py`)*

### `DAYPACK` — the whole day in one file
- **Syntax:** `DAYPACK`
- One .txt attachment for your daily LLM chat: REPORT v4 (upload mode) +
  a FRESH `REPORT NOW` (live prices — expect the ~20-30s) + every document
  uploaded in the last 24h under labeled headers (kind · date · chars).
  Per-doc cap 40k chars (truncated loud); anything over 150k (the Equity
  Hub CSV) is omitted with a note — upload that file to the chat
  separately. Failing sections print their error in place; the pack always
  assembles. Stored to `report_rows` kind=`daypack`.
- *(handler: `tools/daypack.py`)*

### `MOVES` — bucket transitions
- **Syntax:** `MOVES` or `MOVES <n>` (days, default 7)
- Lists roster bucket transitions (`bench → active` etc.) with the quad
  stamped at each move. *(handler: `tools/bucket_history.py`)*

### `SOURCES` — feed health
- **Syntax:** `SOURCES`
- Per-source member count + latest-update date — ingest health at a glance.
  *(handler: `tools/source_registry.py`)*

### `MFR BACKLOG`
- **Syntax:** `MFR BACKLOG` (also `/mfrbacklog`)
- Roster names not yet active in MFR (what to paste into MFR → Activate
  Assets). Read-only — the bot **never writes to MFR** (read API only,
  enroll-never-remove). *(handler: `tools/enrollment.py`)*

### `WRAP` / `WRAP LIST`
- Lists unmapped wrapper proposals (see write form below). Read-only.

---

## 🔴 Write commands (gated)

### `SS:` — Signal Strength roster upload
- **Syntax:** `SS: <TICKER TICKER …>` stages; bot echoes the add/remove
  diff; `CONFIRM` commits (`CONFIRM REPLACE` demanded if the paste removes
  >50% of the roster).
- **Writes:** `ss_roster_history` / `ss_roster_anchor`; every committed diff
  also stamps `ss_flow_events` (structure + quad at event date — the SS flow
  corpus). *(handler: `tools/ss_roster.py`)*

### `QUAD:` — manual GIP quad (the sole canonical quad writer)
- **Syntax:** `QUAD: monthly N quarterly N` (tolerant: `QUAD: M1 Q3`,
  `QUAD: 1 3`; each 1–4) → stages → `CONFIRM` (15-min TTL).
- **Writes:** `hedgeye_quad` + `quad_regime_history` + quad `bot_state` keys.
- **Morning quad check:** when the bot pings `🌅 Quad last confirmed …
  still current?`, reply **`OK`** to stamp it current (value unchanged) or
  send a fresh `QUAD:`. A stray `OK` with no pending ping is ignored.
- *(handlers: `tools/quad_manual.py`, `tools/quad_confirm.py`)*

### `TARGET` — position size targets (identity facts, operator-only)
- **Syntax:** `TARGET LIST` (read-only) · `TARGET <tkr> <pct> [IND|RIRA|ROTH]
  [note]` · `TARGET DEL <tkr> [acct]` · `TARGET CASHEQ <tkr>` /
  `TARGET NOCASHEQ <tkr>` (cash-parking flag: excluded from fills/exposure/
  CONC, counts as parked on the CASH line) — all stage, then
  **`CONFIRM TARGET`** (15-min TTL; `CANCEL TARGET` discards). The confirm
  word is deliberately distinct from bare `CONFIRM` so it can never collide
  with a staged `SS:`/`QUAD:`.
- **Purpose:** fill% = current % of account / target %. Explicit rows are
  operator doctrine and always win; names without a row use the Hedgeye
  asset-class default, printed as `·dflt-*`. Account defaults to IND;
  pct bounds 0–25.
- **Writes:** `position_targets`. Never inferred, never LLM-written.
  *(handler: `tools/position_targets.py`)*

### `WRAP OK` / `WRAP NO` — wrapper linkage (identity facts, operator-only)
- **Syntax:** `WRAP OK <tkr> [underlying] [inverse|long]` · `WRAP NO <tkr>`
- The detector flags wrapper-named book tickers with no linkage row (ingest
  summary + Monday check). `WRAP OK` confirms the proposed mapping (or
  overrides it); `WRAP NO` dismisses permanently. Confirmed links drive
  wrapper trend adjustment, exposure sides, flip-watch, RTA matching.
- **Writes:** `wrapper_links` / `wrapper_no_mapping`.
  *(handler: `tools/wrapper_links.py`)*

### 📥 Document upload — send a file to the bot (no gate; the send IS the action)
- **How:** download a report on your phone (SpotGamma founder's note /
  Flow Patrol / Equity Hub export / Tier One Alpha — PDF, txt, md, csv,
  html), share it to the bot chat like any Telegram attachment. Optional
  caption helps classification.
- **What happens:** text extracted (pypdf for PDFs) → classified
  (`founders_note_am/pm` · `flow_patrol` · `equity_hub` · `tier1alpha` ·
  `other` ⚠) → stored to `doc_uploads` (the RAG staging corpus) → loud
  reply with kind, note date (`UNDATED ⚠` when none found), char count,
  and a preview. 20MB Telegram cap. Unknown binaries store flagged, never
  silently dropped. This replaces the dead 5K Equity-Hub scrape.
- **Screenshots (Vision-OCR):** send photos directly — each is transcribed
  by Claude vision (extraction only) and stored like any document. For a
  MULTI-screenshot report (Tier One Alpha): `DOC START tier one alpha` →
  send all the screenshots (each OCRs into the buffer) → `DOC END` = one
  stitched row. A lone photo stores immediately; caption helps classify.
- **Paste mode (no file needed):** `DOC START [hint]` → paste the whole
  report (Telegram splitting it into 25 messages is fine — the bot buffers
  silently, quiet ack every 10 chunks; pasted lines that look like commands
  are swallowed, never executed) → `DOC END` stitches everything into ONE
  classified `doc_uploads` row with one summary reply. `DOC CANCEL`
  discards; 30-min TTL.
- **Writes:** `doc_uploads` only. *(handler: `tools/doc_ingest.py`)*

### Trade decisions — log against an alert (no gate; the message IS the action)
- `<TICKER> <VERB> [amount]` — e.g. `OIH BUY 100` (links to the ticker's
  most recent alert ≤48h)
- `A<id> <VERB> [amount]` — e.g. `A1234 SELL 250`
- `DONE A<id> [<shares>sh] [@<price>]` (also `FILLED`/`EXECUTED`) — marks
  executed
- **Verbs:** buy, sell, add, trim, long, short, pass, skip, ignore, later,
  wait, hold, override, done, filled, executed. **Amount:** `100`/`$100` =
  dollars; `100sh` = shares. **Writes:** `user_actions`.

### Shared confirmation words

| Word | Effect |
|------|--------|
| `CONFIRM` | Commits whichever is staged (`SS:` or `QUAD:` — only one at a time) |
| `CONFIRM REPLACE` | Confirms an `SS:` upload removing >50% of the roster |
| `CANCEL` | Discards the staged `SS:` or `QUAD:` |
| `OK` | Answers a pending morning quad check (stamps confirmed, no value change) |

---

## 🔔 Alerts the bot sends (not commands — what pushes mean)

**📗 book stamp** — every alert (proactive scanner + price monitor) carries a
book line when the name touches your holdings: `📗 YOU HOLD XLV: LONG` ·
`📗 YOU HOLD SBIT (long shares) = SHORT exposure` · `📗 EXPOSURE via METD
(short META ↯inv)`. Lookup failure prints `📗 book-check FAILED`, never
silence.

**RTA cross-signal** — every Real-Time Alert is matched against your book
(direct / wrapper / shared sector) and the SS roster → one fact-based alert
(matched names, sides, range positions; your rulebook decides). Full closes
(`sell`/`cover`, **not** `-SOME` trims) suppress the name from both alert
universes same-day (`rta_position_closes`); the next publication cycle
governs from tomorrow.

**📗 BOOK DIP / RIP** — market hours, 15-min cycle, holdings only (exempt
from the 20/80 universe filter). CROSSING semantics: a dip is a *retreat*,
not a location — fires only when rp has retreated ≥ `BOOK_DIP_DELTA`
(default **0.25**) from its 5-day max (longs, trend intact BULLISH) or
bounced ≥ delta off the 5-day min (shorts, trend intact BEARISH), and only
when that measure *crosses* the threshold vs the prior cycle. First run
seeds silently — a deploy never floods standing dips. Dedup: one per
(ticker, type) per day.

**⚡ THESIS CHECK (trend flip)** — fires the DAY the linkage-adjusted trend
turns against a position (`SBIT thesis break: BTC flipped BULLISH`). Only
the transition alerts, not the standing condition (standing state shows as
⚠ in `REPORT` / `SCREEN my book …`).

**🔄 Wrapper flip-watch** — once/day: held wrappers whose *underlying* trend
changed since last seen (inverse-adjusted, thesis confirmed/weakening).

**🌅 Morning quad check** — periodic "quad still current?" ping; reply `OK`.

**Env knobs (Railway):** `BOOK_DIP_DELTA` (0.25) · `BOOK_ALERTS_ENABLED`
(1) · `EMAIL_CHECK_INTERVAL` (300).

---

## 🖥️ Operator console (Lenovo, not chat)

**Evening routine — ONE command** (after exporting Positions + Accounts
History from Fidelity to Downloads):

```
python _daily_upload.py
```

Finds the newest `Portfolio_Positions_*.csv` + `Accounts_History*.csv`,
then runs ingest (`--commit`) → actions_log import → outcomes. Loud on
stale files (>3 days), anomalies, or any failing step. Idempotent —
re-running is always safe.

**Vol regime:**

```
python -m tools.vol_regime --show
```

Prints the current 7-sleeve regime line (`--today` writes today's rows;
`--backfill` recomputes history; add `--dry-run` to preview). Nightly write
runs automatically after the MFR fan-out.

**Diagnostics (read-only):** `_wrapper_flag_audit.py` (wrapper ⚠ frames) ·
`_book_sides_verify.py` (position sides + book screens) ·
`_etfpro_health_scan.py` · `_last_upload_dates.py` · `_options_postmortem.py`
(options vs equity P&L). **Tests before committing those areas:**
`test_book_direction.py` · `test_rta_cross_signal.py` · `test_book_alerts.py`.

---

*Background jobs (not chat-triggered): email parsing every 5 min · nightly
MFR to-add + vol-regime write + EOD REPORT row · book alerts market-hours
thread · weekly backlog sweep · quad early-warning · wrapper flip-watch.
COMPLIANCE: Hedgeye is EMAIL-ONLY (never scrape); MFR via read API only;
enroll-never-remove.*
