# Hedgeye Bot — Telegram Command Reference

Generated from the actually-registered handlers in `telegram_handler.py`
(dispatch order: **SS → QUAD → SCREEN → MFR BACKLOG → trade-decision → echo**).
Only messages from your whitelisted chat are processed. Anything unrecognized
just gets echoed back (`Got it: <text>`) — no side effects.

---

## 🟢 Read-only commands (never touch the DB)

### `SCREEN`
- **Syntax:** `SCREEN <plain-english screen>`
- **Purpose:** Natural-language screener over your tagged universe (range position, TREND gate, momentum, in-book).
- **Example:** `SCREEN healthcare longs near the bottom of the range with momentum`
- **Writes DB?** No. *(handler: `tools/screener.py → handle_screen_command`)*

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
