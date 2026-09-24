# WORKSTATION — the whole system on one page
*Handoff doc, updated 2026-09-24. Start any new chat by pointing at this
file. Deep detail: [BOT_COMMANDS.md](BOT_COMMANDS.md) (how to talk to the
bot), [TRADING_DESK_PROMPT.md](TRADING_DESK_PROMPT.md) (the desk-LLM
system prompt), [BUILD_LIST_2026-09-20.md](BUILD_LIST_2026-09-20.md)
(shipped vs open).*

## What this is
Kris trades a ~$85K three-account Fidelity book (Individual X96383748
long+short w/ $5K margin buffer; Rollover 244859926 + Roth 245734604
long-only) on a Hedgeye + MFR + SpotGamma process. Two repos:

- **Bot** `C:\Users\bogac\hedgeye-bot` → Railway (deploy:
  `railway up --detach`, master). Postgres on Railway (local scripts:
  `db_pg._load_dotenv_fallback()`; **always** `PYTHONIOENCODING=utf-8`
  on this Windows box). Telegram interface; iCloud IMAP email relay.
- **Canary** `C:\Users\bogac\Downloads\canary\sg-scraper` → Windows Task
  Scheduler (push: `git push origin main`). Playwright captures; shared
  debug Chrome on :9222 ("Hedgeye+SpotGamma DO NOT CLOSE" console).

The bot computes; the desk-LLM project ("trading desk 2") advises off
uploaded packets; **Kris decides and executes**. Bot never trades.

## Daily rhythm
| when | what |
|---|---|
| ~5-6 AM | Kris drops exports in Downloads → `python _daily_upload.py` (positions + Accounts_History → book/actions/outcomes/cash-flows/**lot ledger**). Weekly-ish: History_for_Account_*.csv (spending) auto-ingests too |
| drive-in | "claude rc" / "**start the watch**" → ritual = RC on + **request_keep_awake(session_idle)** + hourly ScheduleWakeup ticks til 16:00 (memory: watch-loop-keep-awake). Lid must stay open |
| 9:00-16:00 | Popup Sweep every 30 min (kill list incl. Calculator — the cat) |
| 9:05 & 12:00 | hedgeye_links harvest (Cloudflare-polite; 1 challenge = stands down all day) |
| 9:30-9:45 | FlowPatrol(9:30, polls til noon) · Founders(9:32) · Overview(9:34) · Indices+tilt(9:35) · **Digest**(9:36) · **Desk note + NOTE FULL + project docs**(9:40) · BUXX watcher(9:45) |
| 15-min | tape canary (options flow alerts) |
| 18:45 | evening total packet (day PDF, `week_packs\`) |
| Sat 10:00 | walls scorecard (backtests) |
| Sun/Mon | PM change emails auto-parse; **Monday night Kris uploads the ~430-name Position Monitor PDF** → `_pm_ingest.py` dry-run then `--commit` |
| Fri | SS anchor: paste `SS: T1 T2 ...` from roster image + CONFIRM |

## The stack (three layers + rules)
1. **WHAT** — Hedgeye: main monitor (ticker_tags buckets), sector-pro
   monitors (Retail/Financials, carry-forward from Sunday change emails),
   SS roster, ETF Pro, Keith's signals, Capital Allocation, RTAs.
2. **WHERE** — ranges, tier order **hdg > iin (II newsletter, 7d) >
   mfr-published > mfr-derived** (rp_resolve; XLI/ETHA lesson: every
   source has a freshness contract, doctor check #31 sweeps all 16).
3. **HOW** — SpotGamma: EquityHub walls daily 6:15 (gate_walls: hw >35%
   from spot gated; cw<pw = broken map), gamma tilt (**>1.10 pinned /
   <0.90 follow / MIXED between** — sizing gate), tape.

Key doctrine numbers: short entry rp≥0.65 (backtest: pinned days favor
shorts); zone-long adds rp≤0.35 (half-size when pinned); **BREAKOUT
above range top = EXIT bucket for shorts** (ruling pending); sector caps
**15% warn / 25% reject**; starters ~$350 IND / ~$500 IRA; options =
defined-risk verticals, ≤$350 debit; **BUXX $36K/yr, Individual account
ONLY**, buy 0-7d post-distribution (~27th; watcher nudges); shorts are
rented — clock off **oldest open lot**, suppressed while scaling (fills
ledger, 6.9K rows, FIFO lots, reconciles to the export).

## What the bot serves (Telegram)
MARKET / MARKET FULL / MARKET <ETF> (energy complex block: CL1+diesel+
cracks w/ ranges) · NOTE / NOTE FULL · SHELF (covered-short re-entry:
roster+BEARISH+rp≥0.65; ADD BACK when partial) · BOOK RP (**⏱ AS-OF
banner**, lots column, EXIT bucket) · REPORT UPLOAD · EOD (final-bar
repair when Yahoo's close file lags) · SCREEN <anything> (sources:
retail pro, financials pro, capital allocation, ideas, etfpro, keiths,
ss…) · **FILL SOLD X 3 @px** — intraday book bridge (CSV supersedes) ·
RP/CAP/MOVES/TARGET/WRAP/QUAD/SS.

## Known quirks (don't re-diagnose)
- Telegram Desktop downloads fail sometimes → files also live locally
  (`week_packs\`, Downloads); clear Telegram cache to fix.
- Yahoo daily closes can lag mornings → EOD repairs final bar from the
  quote endpoint (provenance-stamped), never banks a stale frame.
- app.hedgeye.com is Cloudflare-gated: harvester never fights it; one
  manual portal open in the **bot Chrome** refreshes clearance (deck).
- The Individual account is ALSO the checking account (~$2K/wk spending)
  — 💳 FLOWS line on the note; **account value ≠ trading P&L**.
- Battery + backgrounded app = frozen session (9/22) → keep-awake is
  part of the watch ritual; closed lid still sleeps.
- VSCO→VSXY (symbol_guard SYMBOL_ALIASES). ~5:30 AM console-kill costs
  one canary cycle, self-heals. The cat opens Calculator.

## Open items (as of 9/24 AM)
- **Kris rulings**: #5 EXIT-on-breakout split (live, confirm) · #7
  ladder X/Y (cover-some X% below last fill; re-short Y% bounce) — last-
  fill now on BOOK RP · #9 bucket order active vs top_idea.
- **Kris actions**: MFR paste HGER + BWET (new positions, dark) + ATUS/
  FYBR/NKLA/ZI/FI stragglers · one portal open for deck capture · Q4
  Macro Themes presentation (invite 9/23) — calendar.
- **Build list**: all-six-index tilt · weekly short-turnover on Friday
  note · SS image-OCR diff · within-layer timestamp ranking · stat-pack
  close-vs-intraday labels · Tier1Alpha vol-control capture · Railway
  dead-man's switch (PC-down alert) · scorecard items #17-23.
