# Hedgeye Bot — Telegram Command Reference

Generated from the actually-registered handlers in `telegram_handler.py`
(dispatch order: **SS → QUAD → SCREEN → MFR BACKLOG → trade-decision → echo**).
Only messages from your whitelisted chat are processed. Anything unrecognized
just gets echoed back (`Got it: <text>`) — no side effects.

---

## 🟢 Read-only commands (never touch the DB)

### `SCREEN`
- **Syntax:** `SCREEN <plain-english screen>` — natural-language screener over your tagged universe.
- **Example:** `SCREEN energy shorts top of range` · `SCREEN etf pro shorts` · `SCREEN keiths longs near the bottom` · `SCREEN signal strength longs`
- **Writes DB?** No — read-only. *(handler: `tools/screener.py → handle_screen_command`)*

**Direction is required** (the TREND gate is tied to it): say `longs` or `shorts`.

**Phrases it understands (mix freely):**
| Phrase | Effect |
|--------|--------|
| `longs` / `shorts` | direction + Rule-1 TREND gate (BULLISH / BEARISH) |
| **source lens** — `etf pro`, `portfolio solutions`, `investing ideas`, `keith's signals`/`keiths`, `signal strength`, `position monitor`, `btc quant`/`bitcoin quant` | screen a whole signal source (not just the tagged roster). No source = tagged roster (`in my book` = your holdings). Header names the source, e.g. `SHORTS · ETF Pro`. `btc quant` = Hedgeye's CRYPTO QUANT trend calls on crypto coins + equities (sided: bullish→long / bearish→short). |
| any GICS sector — `healthcare`, `tech`, `energy`, `financials`, `staples`, `discretionary`, `industrials`, `materials`, `utilities`, `real estate`/`reits`, `communication(s)`, `digital assets`/`crypto` | sector filter |
| `bottom of range` / `near the low` | `near_bottom` (range_pos ≤ 0.20) |
| `top of range` / `near the high` | `near_top` (range_pos ≥ 0.80) |
| `above the cloud` / `in the cloud` / `below the cloud` (aliases: `above/below trend range`) | price vs the MFR **trend range** (the yellow "cloud" band, `ltRangeData`). Rendered per row as `lt:above` / `lt:in=0.42` / `lt:below`. |
| `momentum` / `with momentum` | require bullish MFR momentum |
| `in my book` / `that I own` / `held` | only names you hold |
| `show gated` / `show all` / `include gated` | list the names Rule-1 dropped (see below) |

**All three tiers are screenable**, each row marked: `●● active` · `● top-idea` · `· bench` · `—` untagged (a source name not in the tagged roster).

**Source lenses** are composable with everything (`SCREEN etf pro shorts top of range in my book`). A source shows its WHOLE membership — names not in the tagged roster appear untagged (`—`) and, if un-ranged, in `🌑 DARK`. **Sided sources** carry a stored side rendered as `side:short` per row: `keiths` filters on Keith's stored long/short **and** the TREND gate (`SCREEN keiths shorts` = Keith's short book that's also BEARISH); `etf pro` gains the same once its bias column lands.

**Per-row columns:** `trend·src` (hdg=Hedgeye / mfr=MFR fallback) · `rp` range position · `mom` (BULL/BEAR/NEUT, from MFR — no history wait) · `h` Hurst (>0.5 trending) · `iv rv ivpd` (MFR vol, authoritative) · `cSPY cUUP` (bot-**computed** Pearson vs SPY/UUP daily returns — labeled *calc*, `?` when <20 days). Flags: `⚡DIV(...)` trade-vs-momentum divergence (exhaustion-fade), `📗own` held, `⚠mfr-only` top-idea gated on MFR trend only.

**Nothing disappears silently:**
- `🌑 DARK` — matched your filters but has no MFR range.
- `⛔ GATED BY TREND` — matched the tier but failed Rule-1 (wrong trend). Shown in full with `show gated`, else a one-line count.
- On an empty result, a **funnel** names the first stage that hit 0 (tag match → has-range → TREND → near → …).

### `MFR BACKLOG`
- **Syntax:** `MFR BACKLOG` (also `/mfrbacklog`)
- **Purpose:** Lists roster names not yet active in MFR (tells you what to paste into MFR → Activate Assets). Read-only — never writes to MFR.
- **Example:** `MFR BACKLOG`
- **Writes DB?** No. *(handler: `tools/enrollment.py → handle_backlog_command`)*

---

## 🔴 Write commands (change the DB — gated)

### `SS:` — Signal Strength roster upload
- **Syntax:** `SS: <TICKER TICKER …>` to stage, then `CONFIRM` to commit.
- **Purpose:** Replace the current Signal Strength roster with the pasted list.
- **Example:**
  - `SS: AMAT RSI CZR …` → bot echoes the parsed add/remove diff and waits.
  - `CONFIRM` → writes the roster.
- **Writes DB?** **Yes** → `ss_roster_history` / `ss_roster_anchor`.
- **Confirmation gate:** `CONFIRM`. If the paste would remove **>50%** of the roster, it demands `CONFIRM REPLACE` instead. The `SS:` paste alone only *stages* (writes a pending marker); nothing lands until you confirm.
- *(handler: `tools/ss_roster.py → handle_telegram_text`)*

### `QUAD:` — manual GIP quad
- **Syntax:** `QUAD: monthly N quarterly N` to stage, then `CONFIRM` to commit. Tolerant forms: `QUAD: M1 Q3`, `QUAD: 1 3`. Each number must be **1–4**.
- **Purpose:** Set the canonical monthly/quarterly Quad by hand (the sole canonical quad writer).
- **Example:**
  - `QUAD: monthly 1 quarterly 3` → bot echoes “Set monthly Quad 1 / quarterly Quad 3? Reply CONFIRM”.
  - `CONFIRM` → writes it.
- **Writes DB?** **Yes** → `hedgeye_quad` + `quad_regime_history` + `bot_state` quad keys.
- **Confirmation gate:** `CONFIRM` (also `CONFIRM QUAD`), with a **15-minute TTL** on the staged value.
- *(handler: `tools/quad_manual.py → handle_quad_command`)*

### Trade decisions — log/execute against an alert
- **Syntax (three forms):**
  - `<TICKER> <VERB> [amount]` — e.g. `OIH BUY 100` (links to the ticker's most recent alert, ≤48h)
  - `A<id> <VERB> [amount]` — e.g. `A1234 SELL 250` (links to a specific alert id)
  - `DONE A<id> [<shares>sh] [@<price>]` — e.g. `DONE A1234 100sh @ 419.50` (also `FILLED` / `EXECUTED`) marks the action executed
- **Verbs:** buy, sell, add, trim, long, short, pass, skip, ignore, later, wait, hold, override, done, filled, executed.
- **Amount:** `100` or `$100` → dollars; `100sh` / `100 shares` → shares.
- **Purpose:** Record your decision (and later fill) on an alert into the decision log.
- **Writes DB?** **Yes** → `user_actions` (the DONE form updates the row to executed).
- **Confirmation gate:** **None** — the message itself is the action; it logs immediately.
- *(handler: `telegram_handler.py → parse_decision` / `handle_decision`)*

---

## Shared confirmation words

| Word | Effect |
|------|--------|
| `CONFIRM` | Commits whichever is currently staged — an `SS:` upload **or** a `QUAD:` value. Only one can be staged at a time (staging one clears the other), so there's no ambiguity. |
| `CONFIRM REPLACE` | Confirms an `SS:` upload that removes >50% of the roster. |
| `CANCEL` | Discards the staged `SS:` or `QUAD:`. |

---

*Not covered here because they aren't chat commands: scheduled background jobs
(nightly MFR to-add, weekly backlog sweep, quad early-warning, email parsing) and
the separate `command_bridge` script-runner — none are triggered by typing in chat.*
