# HOW TO TALK TO THE BOT — the command map

Text any of these to the Telegram bot. Commands are case-insensitive.
Replies over 4,096 chars arrive as `.txt`/`.pdf` documents — those are the
LLM-ready packets: forward or upload them as-is.

## THE MORNING READS (chat cards, seconds)

| say | you get |
|---|---|
| `MARKET` (or `MKT`) | whole-market snapshot mirroring the Position Monitor: sector ETFs over each Hedgeye long/short group, ranges, live px, ⋄dealer walls, gamma regime (FADE/FOLLOW) + the day's backtest hint |
| `MARKET FULL` | same, as a .txt pack with ALL ~440 monitor names incl. bench — the LLM feed |
| `MARKET XLE` (any sector ETF) | one-sector drill-down: every monitor name under it with ranges + walls |
| `NOTE` | the institutional desk note: stance, exposure, attribution, deltas, catalysts, flagged positions (also auto-sent 9:40 AM) |
| `NOTE FULL` | desk note as .txt with a thesis card for EVERY position |
| `KEITH` | Keith-pattern stage snapshot (`KEITH STRICT` tighter, `KEITH WEEKLY` forces the Friday report) |

## THE BIG PACKETS (documents for the desk LLM)

| say | you get |
|---|---|
| `DAYPACK` | today's everything in one .txt — upload to the day chat |
| `REPORT` | v4 compact book report in chat (`REPORT FULL` adds unfiltered divergences) |
| `REPORT UPLOAD` | the verbose book report as .txt — full targets/sources/DIV + position table + SG walls. THE one to paste into an LLM |
| `BOOK FULL` | per-account position table as .txt |
| `BOOK RP` | whole book with range-position per line, dark rows last |
| `WEEKEND` (or `ROTATION`) | full-universe weekend rotation report |
| `EOD` (or `STAT PACK`) | end-of-day stat pack |
| `SCORECARD` (or `SHADOW`) | shadow scorecard — how the calls graded out |

Auto-deliveries (no command needed): every Hedgeye email relayed as it
lands · morning digest 9:38 · desk note 9:40 · BUXX nudge in the window ·
evening total packet 6:45 PM · Saturday walls scorecard 10:00.

## QUICK LOOKUPS

| say | you get |
|---|---|
| `RP SMH` (any ticker) | single-name range position, inline |
| `SHELF` | the watch shelf: covered shorts eligible to re-rent — 🔔 fires (roster + BEARISH + rp≥0.65), watching list, expiries, lifetime stats. Fires also ride the 9:40 note |
| `CAP PSX 500` | pre-trade check: does $500 more PSX bust a sector/country cap? (`CAP <tkr> [dollars] [account]`) |
| `MOVES` / `MOVES 14` | bucket transitions in the last 7 / n days |
| `MFR COVERAGE` | wanted vs enrolled vs served — the range-feed health check |
| `SOURCES` | what research sources are wired in |

## SCREENS (natural language, no LLM — pure filters)

`SCREEN` + plain words: sector, side, position in range, source, held.

- `SCREEN energy longs near the low`
- `SCREEN shorts near the top`
- `SCREEN held momentum`
- `SCREEN etf pro longs` · `SCREEN keiths` · `SCREEN signal strength`
- `SCREEN retail pro shorts` · `SCREEN financials pro longs` ·
  `SCREEN capital allocation` (the sector-pro rosters — also as SECTOR
  PRO overlays on `MARKET XLY` / `MARKET XLF`)
- `SCREEN everything financials` (full universe, not just the monitor)
- add `show gated` to include stale-range names

After a screen replies, short follow-ups refine it: `near the low`,
`show gated`, `held only`. Unknown words are named, never ignored.

## FILLS — keep the book live intraday (9/22: sheets were a day stale)

| say | effect |
|---|---|
| `FILL SOLD COP 3.7 @128.26` | book updates in seconds — BOOK RP / REPORT / screens / shelf all see it |
| `FILL BOUGHT BNO 8.5 @58.61` · `FILL SHORTED WSM 1` · `FILL COVERED ACI 10` | same, all verbs |
| add `RIRA` or `ROTH` | non-Individual account (IRAs reject shorts) |
| `FILL LIST` / `FILL UNDO` | today's texted fills / delete the last one |

A texted fill is a BRIDGE, not a record — tomorrow's CSV upload
supersedes it automatically. Price optional.

## LOGGING TRADES (so the clock and attribution stay true)

Plain verb lines — the bot logs them against alerts/positions:

- `OIH BUY 100` · `PSX SELL 250` · `SMH SHORT 300` (ticker verb amount —
  dollars, or `sh` for shares)
- `A1234 BUY 500` (answering a specific alert id)
- `DONE 1234 3.2 @ 155.10` (execution fill vs an alert)
- Verbs: buy, sell, add, trim, long, short, cover, pass...

## FEEDING IT RESEARCH (uploads)

| flow | how |
|---|---|
| PDFs / CSVs / screenshots | just send the file or photo — it's classified and stored (photos get OCR'd) |
| Multi-screenshot doc | `DOC START <hint>` → send shots/pastes → `DOC END` (stitched into one document) |
| Signal Strength roster | paste `SS: TICK1 TICK2 ...` → bot stages and shows the diff → reply `CONFIRM` (or `CONFIRM REPLACE` if it shrinks a lot) / `CANCEL` |
| Quad call | `QUAD: ...` → `CONFIRM QUAD` / `CANCEL` |
| Fidelity exports | on the PC: `python _daily_upload.py` (not Telegram) |

## GUARDRAILS & SETTINGS

| say | you get |
|---|---|
| `TARGET LIST` | position-size targets + cash-equivalents + doctrine |
| `TARGET CPAY 3` / `TARGET CPAY 3 IRA` | set a name's max % (reply `CONFIRM TARGET`) |
| `TARGET CASHEQ CLOX` / `TARGET NOCASHEQ CLOX` | mark/unmark cash-like |
| `WRAP` / `WRAP LIST` | wrapper-ETF link proposals (`WRAP OK <tkr>` / `WRAP NO <tkr>`) |
| `MFR BACKLOG` | enrollment backlog (`MFR BACKLOG WHY` / `FORCE`) |

## PROJECT-KNOWLEDGE REFRESH (for the desk-LLM project)

`current-book.md` + `buxx-ledger.md` regenerate daily at 9:40 and land in
Telegram every **Monday** — re-upload them to the desk project so the
short-inventory clock has fresh entry dates. On the PC anytime:
`python tools/book_export.py --send`.

---
Anything that matches nothing above is echoed back — if the bot just
echoes you, it didn't understand: check this map.
