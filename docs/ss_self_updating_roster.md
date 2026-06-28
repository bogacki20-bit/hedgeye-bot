# Self-updating Signal Strength roster (Priority 0) — design

Status: **design approved; building one step at a time.** No implementation beyond the
step explicitly built. Supersedes the OCR-anchor idea with a **weekly human anchor**.

## Problem
The full SS list is image-only in the email; the text gives only daily Add/Remove
deltas. Applying deltas to a drifted base caused the original phantom bug. Fix: a
**weekly human ground-truth anchor** + **daily deltas on top**, so max drift = one week.

## Locked decisions (2026-06-28)
- **Build order:** deltas before the upload handler (the diff-squawk is only meaningful
  once deltas are running to diff against).
- **CONFIRM handshake stays** — guards against a garbled paste overwriting the roster.
- **DB table is authoritative; `ss_full_list.yaml` is break-glass fallback.**

## Data flow
```
FRIDAY ~6:30-7pm ET (after that day's SS email is parsed):
  bot --Telegram--> "Upload this week's SS list" prompt
  you --Telegram--> paste the full list
  bot: parse -> echo "got N, diff vs deltas: +X -Y" -> you reply CONFIRM
       -> declarative reconcile of ss_roster_history (source='anchor')
       -> record ss_roster_anchor + squawk the drift diff
       -> bot_state.ss_last_anchor_date = today

MON-THU (each SS email):
  parser_signal_strength (text deltas) -> hedgeye_signal_strength        [EXISTS]
  +-[NEW] applier: apply Add/Remove to ss_roster_history (source='delta')
          + count-vs-"N Stocks" check -> squawk on mismatch

ALWAYS:
  ss_roster_history (removed_on IS NULL) = canonical roster
  +- active_slice signal_strength bucket reads it LIVE (no redeploy)
  MFR enrollment = SEPARATE, untouched (writes ZERO to MFR)
```

## 1. Upload mechanism — paste text + CONFIRM
- **Paste text, not a file** (80 tickers ≈ 500 chars, far under Telegram's ~4096 limit;
  no download/MIME plumbing).
- **Disambiguation** from trade replies: reply to the bot's prompt (`reply_to_message_id`)
  or prefix `SS:`. Bot's prompt states the convention.
- **CONFIRM handshake:** parse (tolerate commas/spaces/newlines, uppercase, dedup,
  validate `^[A-Z]{1,5}$`) -> echo count + ignored tokens + diff vs current roster ->
  commit only on `CONFIRM`; discard if no confirm within ~15 min. The upload becomes
  authoritative, so this guard is load-bearing.
- Reuses the existing Telegram listener (`start_telegram_listener`).

## 2. Canonical roster location — DB table (`ss_roster_history`)
- `active_slice` reads it **live each cache cycle -> no redeploy** on upload/delta. (A
  YAML changes only via git push + Railway redeploy; a runtime YAML rewrite on Railway is
  ephemeral.) Transactional with the history table; one auditable source of truth.
- **Precedence:** `ss_roster_current` (DB) -> `ss_full_list.yaml` (manual override) ->
  delta reconstruction (last resort).

## 3. Schema
```sql
CREATE TABLE ss_roster_history (
    id            BIGSERIAL PRIMARY KEY,
    ticker        TEXT NOT NULL,
    added_on      DATE NOT NULL,
    removed_on    DATE,                 -- NULL = currently on the roster
    add_source    TEXT NOT NULL,        -- 'seed' | 'anchor' | 'delta'
    remove_source TEXT,                 -- 'anchor' | 'delta'
    anchor_id     BIGINT,
    source_email_id TEXT,               -- email that drove a delta-add (migration 040)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ss_roster_one_open ON ss_roster_history (ticker) WHERE removed_on IS NULL;

CREATE VIEW ss_roster_current AS
    SELECT ticker, added_on, add_source FROM ss_roster_history WHERE removed_on IS NULL;

CREATE TABLE ss_roster_anchor (
    id            BIGSERIAL PRIMARY KEY,
    anchor_date   DATE NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ticker_count  INT NOT NULL,
    tickers       JSONB NOT NULL,
    roster_before INT,
    diff_added    JSONB,
    diff_removed  JSONB,
    note          TEXT
);
```
**Removed-name handling:** a remove (delta or anchor) closes the open row
(`removed_on = date`) -> drops from polling immediately; dated history preserved; **MFR
untouched** (handler/applier write only to `ss_roster_history`). Re-add = new open row.

## 4. Friday collision — delta (~afternoon) + anchor (~6:30pm) same day
No conflict, because they are different operations:
- **Deltas are additive** (`+X`/`-Y` on the current roster).
- **The anchor is declarative** — the upload defines exact membership; CONFIRM computes
  `diff = upload vs current roster` and applies only that diff (open missing, close extra,
  leave matches). Re-adding an already-open ticker is a no-op (the `one_open` unique index
  guarantees it). **Double-counting is impossible.**

Sequencing guarantee (anchor is the day's last write):
1. Friday SS email parsed -> deltas applied (`source='delta'`).
2. Prompt fires ~6:30pm ET **only after** confirming today's SS email is parsed (prompt
   thread checks `hedgeye_emails_raw` for a parsed SS email dated today; if not yet, it
   delays/falls to the re-ping ladder). Anchor therefore reconciles on top of the deltas.
3. Upload -> CONFIRM -> declarative reconcile + diff-squawk ("upload=80, deltas=80,
   differed: none" on a clean week).
4. No SS email Sat/Sun; Monday's delta applies on top of Friday's anchor (intended).

Idempotency: re-parsing Friday's email or re-applying the anchor are both no-ops.

## 5. Missed Friday — graceful degrade + re-ping
- Nothing breaks: roster runs on **last anchor + ongoing deltas**; drift grows past one
  week until the next anchor.
- Uploads accepted **any day** (Friday is only the prompt cadence); a late upload resets
  the clock.
- **Re-ping ladder** (driven by `bot_state.ss_last_anchor_date`): no anchor by Friday EOD
  -> daily reminder, throttled once/day, until an anchor lands or the next Friday.

## Doctor checks (fail loud)
- **`ss_anchor_stale`** — FAIL if `today - ss_last_anchor_date > N days` (N=10: tolerates
  one missed Friday, escalates on two).
- **`ss_roster_count`** — roster count == latest email "N Stocks" (same-day delta-miss tripwire).
- **`ss_roster_vs_scan`** — running bot's `scanner_last_source_flags` SS-tags subset of
  current roster, fresh in market hours (generalizes the one-shot `ss_roster_check.py`).

## Where each piece lives
| Piece | Location |
|---|---|
| Roster + anchor tables + view | bot Postgres (`migrations/039_ss_roster.sql`) |
| Delta applier + count check | bot `tools/ss_roster.py`, called by `parser_signal_strength.process_email` |
| Telegram upload handler | bot — new branch in the telegram listener |
| Friday prompt + re-ping ladder | bot — thread in `main.py` (pattern of `_mfr_watchlist_loop`), ET-aware |
| `active_slice` read | bot — SS bucket reads `ss_roster_current`, YAML/deltas fallback |
| Doctor checks | canary `doctor.py` (retire/repoint one-shot `ss_roster_check.py`) |

## Build order (each independently shippable + reversible)
1. **Migration + seed** the table from today's 80 (`add_source='seed'`) + view. No behavior change.
2. **`active_slice` -> `ss_roster_current`** (fallback YAML->deltas). DB authoritative; live updates.
3. **Delta applier** on each SS email + count-vs-header squawk. Self-update Mon-Thu.
4. **Telegram upload handler** (paste -> echo -> CONFIRM -> anchor reconcile + diff squawk).
5. **Friday prompt + re-ping ladder.** Automates the cadence.
6. **Doctor `ss_anchor_stale`** (+ wire count/scan checks). Loud on a missed anchor.

Notes: no OCR / external API / new cost. CONFIRM is load-bearing. Max drift bounded to one
week by the weekly anchor; the count-check catches most delta misses same-day.
