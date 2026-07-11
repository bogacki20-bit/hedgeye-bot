# HEDGEYE-BOT — WEEKEND BUILD HANDOFF (as of Sat 2026-07-11, EOD)

Paste this whole file into a fresh Claude session to continue building.
Weekends are the only real build time — weekdays Kris only runs the routine
(see OPERATOR_ROUTINE.md in repo root).

## WHO / WHERE / FLOW
- Operator: Kris. Bot repo: C:\Projects\hedgeye-bot on the Lenovo (Windows).
  GitHub bogacki20-bit/hedgeye-bot → Railway auto-deploys master. DB = Railway
  Postgres (DATABASE_PUBLIC_URL in .env; db_pg.py has a .env fallback).
  Canary/doctor: C:\Users\bogac\Downloads\canary\sg-scraper (Windows scheduler,
  no deploy). Old copy at C:\Users\bogac\hedgeye-bot is INERT — ignore/delete.
- Interface: Telegram ("Hedgeye stopgap bot"). Email poll now every 5 min
  (EMAIL_CHECK_INTERVAL=300 on Railway).

## HOW TO WORK WITH KRIS (learned the hard way)
- ONE command at a time, in a plain code block, nothing before the word
  `python`/`git`. His terminal is old PowerShell: multi-line pastes glue
  together, transcripts get pasted back by accident (harmless, tell him so).
- Never one-liner python in PowerShell (quote mangling) — always write a small
  script file (_apply_0XX.py pattern: apply migration + DRY-RUN preview).
- Flow per feature: build + fixture tests → he runs apply script (dry-run) →
  eyeball together → real run → git add/commit/push (Railway deploys).
  He executes everything = the CONFIRM gate.
- If his paste arrives blank/truncated, say so immediately; never narrate
  unseen output.

## HARD RULES (unchanged)
Python owns ALL computation and DB writes; LLM reasons over precomputed facts
only. Loud CONFIRM gates on writes; no silent failures; a fact without a date
isn't a fact; labels only from actual table membership; identity facts
(quad, wrapper links) operator-confirmed only. COMPLIANCE: Hedgeye EMAIL-ONLY
(never scrape), MFR via read API only, enroll-never-remove.

## SHIPPED SAT 7/11 (do not rebuild — verify if suspicious)
Commits e512db2 → 3f3cecb, migrations 055–060, all deployed:
1. position_direction: SCREEN "book shorts/longs" = POSITION side (signed
   qty, options net-of-spread, inverse wrappers). tools/book_direction.py.
   Fixed the SHY/TUA/HEFT/AGGH-shown-as-shorts bug.
2. 📗 book stamp on every alert (proactive_scanner + price_monitor).
3. RTA cross-signal (tools/rta_cross_signal.py, migration 055): every
   Real-Time Alert matches to book (direct/wrapper/sector) + SS roster →
   fact alert; full closes (sell/cover, NOT -SOME) suppress the name from
   both alert universes same-day (rta_position_closes).
4. PS flow corpus (tools/ps_flow.py, 056): 220 stamped add/drop events,
   structure frozen at event date; auto-stamps every PS email. Quad stamps
   NULL before 2026-07-06 (QUAD_CLEAN_START — historical quads were wrong).
5. SS flow corpus (tools/ss_flow.py, 058): 32 events stamped; hooks
   apply_deltas; churn_summary() line.
6. Vol-regime layer (tools/vol_regime.py, 057): 7 sleeves (VIX via VX_F
   fallback until spot ^VIX data flows — enrolled on MFR 7/11 along with
   ^VVIX; verify snapshots appeared), nightly write after MFR fan-out,
   regime_line() header. Backfilled 39 dates.
7. Book alerts (tools/book_alerts.py, 059): dip/rip CROSSING semantics
   (fires only when retreat crosses BOOK_DIP_DELTA=0.25 vs prior cycle;
   first run seeds silently — no floods) + trend-flip-on-transition.
   Market-hours thread in main.py, 15 min.
8. REPORT command (tools/report.py, 060): quad+vol header, sector flow,
   RANGE DYNAMICS (HH/HL etc), dollar+bonds, SS churn, book flags, ⚡DIV,
   alert counts. Telegram sentinel REPORT; nightly EOD row stored
   (report_rows = ML corpus).
9. compute_outcomes v2: signed shorts, option 100× multiplier, gap-sell
   exclusion, --rebuild. Corpus rebuilt: 3,940 honest round trips.
   actions_log backfilled to Jan 2 (was stale since 5/18).
10. Fidelity ingest fixes: 2026-07 header casing, .env DSN fallback,
    per-date quad cache. _daily_upload.py = ONE evening command (finds
    newest exports in Downloads, runs whole chain).
11. Facts established: Kris's real return +10.62% TWR (trailing yr),
    ~5pts lost to options (his read, corpus-consistent); Individual acct
    cycles ~$160k/yr of deposits/withdrawals (payroll+spending) — never
    judge by balance.

## FIRST TASKS NEXT SESSION (in order)
1. **Wrapper-flag audit** (open bug): after the double-flip fix, SBIT/EUO
   flag correctly but METD/GGLS/MSFD/YCS/SQQQ show NO ⚠ in book_alerts/
   REPORT despite broken theses per SCREEN; pre-fix book counts (58L/5S)
   also disagreed with SCREEN's 10 exposure-shorts. Write a diagnostic
   dumping side/raw_side/trend_dir per wrapper from tools/book_alerts.
   _book_rows; align with screener frame; fixture all five.
2. **UPDATED COMMAND KEY** (Kris explicitly wants this): rewrite COMMANDS.md
   + a printable card v3 covering everything new: REPORT · SCREEN book
   semantics (position side, ⚠️trend-against, side:, ◻️ indeterminate) ·
   📗 alert stamps · RTA cross-signal alerts + same-day removal · book
   dip/rip + trend-flip alerts (and their knobs) · SS:/QUAD:/CONFIRM/WRAP
   OK|NO/MOVES/SOURCES/MFR BACKLOG (existing) · python -m tools.vol_regime
   --show · _daily_upload.py evening routine. COMMANDS.md is source of
   truth; card is for printing.
3. **REPORT NOW** (intraday variant): live yfinance prices vs stored ranges,
   names at top/bottom 15% now, book positions near edges.
4. **Native macro symbols** (queue #2): needs KRIS's CONFIRM per mapping
   (BITCOIN→BTCUSD, GOLD→? — careful: GOLD the ticker = Barrick. Identity
   facts = operator-confirmed only).
5. Then remaining queue: full-universe tagging / quad-doctrine table /
   style factors (#6), Telegram write-path + CSV upload (#7), BTC Quant
   deepening (#8), Databento (#9), ML layer (#10). Also deferred:
   outcomes v3 expiration handling (no expiry rows in his data yet);
   SS PNG Vision-OCR anchor (SIGNAL_STRENGTH_TODO.md Priority 1).

## DATED
- Mon 7/13: Feed 2 verdict (PM anchor vs week of dry-run deltas).
- ~week of 7/13: review 20/80 suppression counts before 15/85 decision.
- Jul 17: option expiries — book currently holds ZERO options; if he
  re-enters, expiry-watch build becomes urgent.
- Telegram pending: WRAP NO DRIP if not yet answered (EXP match is a
  description misparse — NOT Eagle Materials; real underlying ≈ XOP inv 2x).
- Verify VVIX + ^VIX snapshots started flowing (enrolled 7/11); vol_regime
  auto-prefers spot ^VIX over VX_F per-date once data exists.

## TEST FILES (run before any commit touching these areas)
test_book_direction.py · test_rta_cross_signal.py · test_book_alerts.py
Diagnostics: _book_sides_verify.py · _etfpro_health_scan.py ·
_options_postmortem.py (options vs equity P&L — Kris hasn't run it yet) ·
_last_upload_dates.py · _daily_upload.py
