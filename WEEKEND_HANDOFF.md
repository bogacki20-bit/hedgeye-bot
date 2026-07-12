# HEDGEYE-BOT — WEEKEND BUILD HANDOFF (as of Sat 2026-07-11, late)
Paste this whole file into a fresh Claude session to continue building.
Weekends are the only real build time — weekdays Kris only runs the routine
(see OPERATOR_ROUTINE.md in repo root).

## WHO / WHERE / FLOW
- Operator: Kris. Bot repo: C:\Projects\hedgeye-bot on the Lenovo (Windows).
  GitHub bogacki20-bit/hedgeye-bot → Railway auto-deploys master. DB = Railway
  Postgres (DATABASE_PUBLIC_URL in .env; db_pg.py has a .env fallback).
  Canary/doctor: C:\Users\bogac\Downloads\canary\sg-scraper (Windows scheduler,
  no deploy) → GitHub bogacki20-bit/hedgeye-canary (both repos pushed, the
  old "local-only" note was stale). Old copy at C:\Users\bogac\hedgeye-bot
  is INERT — ignore/delete.
- Interface: Telegram ("Hedgeye stopgap bot"). Email poll every 5 min
  (EMAIL_CHECK_INTERVAL=300 on Railway).

## HOW TO WORK WITH KRIS (learned the hard way)
- ONE command at a time, in a plain code block, nothing before the word
  `python`/`git`. Old PowerShell: multi-line pastes glue together,
  transcripts get pasted back by accident (harmless, tell him so).
- Never one-liner python in PowerShell — always a script file
  (_apply_0XX.py pattern: migration + SIMULATED dry-run preview + --commit).
- Flow per feature: build + fixture tests → dry run (SIMULATED post-seed
  state, in-memory only) → eyeball together → --commit → git add/commit/push.
  He executes everything = the CONFIRM gate.
- If his paste arrives blank/truncated, say so immediately; never narrate
  unseen output. Watch for him running commands in the WRONG FOLDER (bot vs
  canary) — check the PS prompt path.

## HARD RULES (unchanged)
Python owns ALL computation and DB writes; LLM reasons over precomputed facts
only. Loud CONFIRM gates on writes; no silent failures (n/a always printed
with a reason); a fact without a date isn't a fact; labels only from actual
table membership; identity facts (quad, wrapper links, position targets,
cash-equivalents) operator-confirmed only. COMPLIANCE: Hedgeye EMAIL-ONLY
(never scrape), MFR via read API only, enroll-never-remove.

## SHIPPED SAT 7/11 (do not rebuild — verify if suspicious)
Morning session (commits e512db2→3f3cecb, migrations 055–060): see git log —
position_direction/book stamps/RTA cross-signal/PS+SS flow corpora/vol-regime
/book dip-rip alerts/REPORT v1/compute_outcomes v2/ingest fixes.

Evening session (commits 2132efa, f223755, 518a4e7 — all deployed):
1. Wrapper-flag "bug" CLOSED: no defect. METD/GGLS/MSFD/YCS/SQQQ absent
   because Kris CLOSED them; frames agree everywhere (_wrapper_flag_audit.py
   = rerunnable diagnostic). REPORT BOOK counts fixed to EXPOSURE frame
   (matches SCREEN book shorts; raw-frame ⚠ verdict untouched).
2. COMMANDS.md rewritten + COMMAND_CARD.html (printable card v3, print via
   browser Ctrl+P). COMMANDS.md = source of truth; keep both in sync.
3. REPORT v4.1 (the big one — migrations 061/062/063, all applied+seeded):
   - Δ-since-last header (report_snapshots state chain — WORKING, verified)
   - SECTOR FLOW ✓/✗ flow-quality marks (✗ = rp rising in descending range
     = fade — the manual XLB/XLI/XLU catch, now computed)
   - BOOK flags carry fill context: (L,rp0.27,BULL,1.2%acct,60%,RIRA,+3.5%pl)
   - TRANCHE v2: fill% = acct% / target%. position_targets table
     (identity facts). Default tiers: fi 10 / core 4 / eq 2 / sat 1
     (shorts→sat); routing from Fidelity descriptions incl. abbreviations
     (TREAS/TRS/BD/FD); fund naming REQUIRED (GOLD-ticker lesson).
     Buckets <40 STARTER · <80 BUILDING · ≤110 FULL · >110 OVER.
     Seeded: FUTY 4%/RIRA · BNDD 10%/IND · TUA 10%/RIRA · ULS 2%/RIRA.
     Target-sum sanity: 87% of account value ✓.
   - cash_equivalent flag (ticker_tags col): BUXX seeded ($13,571 parked —
     NOT the ~$21K Kris guessed; snapshot says 13.6k). SHY deliberately NOT
     flagged (⚠ stays on purpose). CASH line: settled+parked=deployable
     ($47,171 / 52.3% AUM).
   - Multi-account: per-(ticker,account) fills + TOTAL agg rows
     (CLOX/SN/ZROZ split; agg fill = Σmv / Σ target dollars).
   - Two renders, one compute: compact Telegram (3329 chars, <3500 ✓;
     tgt shown only when explicit; DIV auto-collapses to count >3500) vs
     REPORT UPLOAD verbose .txt attachment (9.5k chars; full tgt/src + DIV
     + position table). BOOK FULL = table as .txt. sendDocument plumbing
     in telegram_handler (dict replies).
   - CANDIDATES rule: TREND=BULL + rp<0.35 + fill<80% (held names show
     fill). REPORT LEGACY = v3 renderer, parallel-run ~1 week then remove.
   - Telegram: TARGET LIST / TARGET <tkr> <pct> [acct] [note] / TARGET DEL
     / TARGET CASHEQ|NOCASHEQ <tkr> → literal CONFIRM TARGET (NOT bare
     CONFIRM — SS/QUAD cross-clear doesn't know this module; deliberate).
4. OVER after calibration = real trim signals: UNH 153 · BRKR 152 ·
   CLOX 134 (doubled IND+ROTH) · WM 117. AT-MAX: BNDD 94 EUO 108 HD 83
   HEFT 96 LLY 99 ROK 87 RSG 95 TOL 102 VVV 82 WCN 95 XLRE 103.
5. 36 GUESSED tiers remain (no ticker_tags row — tier from description
   alone): AGGH ARKG BRKR CBRL CLOX DESK DRIP EUO EWH FAB FXH HEFT HST IAK
   IWO JETS NORW PALL PAVE SBIT SETH SHY TLT TOL UUP VCLT VMC VXF VYM XAR
   XHB XLP XLRE XLU XLV ZROZ. Verify opportunistically via TARGET rows.
   Reroute-proposal scanner lives in _apply_063.py (patterns: stock-routed-
   fund, sector-routed-sat) — reported none outstanding.

Late session (commits fe42467, 1522732, 8099c50, 1df3a2e — all deployed):
6. **REPORT NOW live** (sprint P1 complete, incl. an LLM review pass):
   full-MFR universe w/ defined signal, ONE batched yf.download (per-name
   pacing rate-limits ~300; MFR latestPrice fallback capped at 5), rotation
   cues = the EOD sector-flow Δ3d/✓✗ math bridged ETF→GICS, LONGS from hot
   / SHORTS from cooling (IND-only), grouped BY CUE with fade marks
   (XLB✗: SHW …| XLY✓: BYD …), tier dots ●●/●, share-class collapse
   (PBR/-A 0.75-0.95), named price-misses, sizing header (L 100bps add
   50/100 · ETF 4%(6⚠) · S HARD 2%/50bps), flags ⚠cap4/⚠ceil6/⚠HARD2 —
   surface, never hide. EDGES = book at live extremes (unclamped rp).
   ~20-30s synchronous, ~750 chars. tools/report_now.py, kind='now'.
   VSCO→VSXY mapped in HEDGEYE_TO_YFINANCE (rename, operator-confirmed);
   GLASF/VRNO still unpriceable — operator to confirm OTC symbols.
7. **Document ingest live** (sprint P2 complete): send a FILE to the bot
   (getFile download, pypdf added to requirements) OR paste mode —
   `DOC START [hint]` → paste (multi-message OK; buffer handler runs FIRST
   in dispatch and claims command-lookalikes) → `DOC END` = ONE classified
   doc_uploads row (migration 064). Kinds: founders_note_am/pm ·
   flow_patrol · equity_hub · tier1alpha · other⚠. note_date parsed,
   UNDATED loud. doc_uploads = RAG staging corpus. The 5K Equity-Hub
   scrape is DEAD (operator-killed; nothing existed in bot repo anyway).
   Lesson learned live: operator pasted Equity Hub as 25 raw messages
   BEFORE paste mode existed — those hit the echo handler, harmless, but
   the paste flow exists precisely for this.

Post-sprint session (commits 408afae→, Sat night):
8. **T1A deep parse** (tools/t1a_parse.py, migration 065): regimes from
   PROSE (dial selection doesn't survive OCR), levels + flip-distance
   (Python math on clean prices), ratio fields stored RAW + scale_suspect
   (OCR eats decimals), econ events. Auto-parses on tier1alpha upload
   (ingest hook); T1A fact line in REPORT → rides into DAYPACK. First row
   committed (7/10: gamma POSITIVE, flip 7456 +1.2%, throttle 7.27,
   systematics BUYERS, strategic NEUTRAL, CPI 7/14 ~1.13%). Date parser:
   TEXTUAL dates now beat numeric axis noise (doc-5 lesson). doc 5 = a
   degraded duplicate of the 7/10 report (skipped via --skip; ages out).
9. **KEITH SS-drop invalidation**: ✗SSdrop@date tags in KEITH + weekly
   (flagged, never hidden).
10. **DAYPACK Equity Hub EXTRACT**: held+index rows distilled from the
    1.3M-char CSV INSIDE the pack (structure-defensive column voting) +
    latest-per-kind dedupe with loud skip note. No new commands — upload
    then DAYPACK, done. Pack ≈54k chars w/ everything.
11. LLM economics settled: classifier GATED OFF (CLASSIFIER_ENABLED=1 to
    revive, ~$8-24/mo); OCR on (pennies, operator-initiated); notifier
    untouched (~$13/wk, WATCH via _llm_ledger.py — 5x'd this week);
    heavyweight recommender still unreachable (5/24 rollback confirmed).
    Retired-model fix: sonnet-4-20250514 404s; classifier→sonnet-5 (gated),
    OCR→haiku-4.5, decision_engine sonnet-4-5 alive.

## POST-DEPLOY CHECKS (do first next session if not yet done)
- Telegram: REPORT (~3.3k, 1 msg) · REPORT NOW (~25s, grouped cues) ·
  REPORT UPLOAD (.txt attach) · TARGET LIST (4 rows + BUXX casheq) ·
  BOOK FULL (.txt) · DOC START/paste/DOC END with a real Equity Hub paste
  (operator may have already tested file-send — confirm a doc_uploads row
  exists: kind, date, chars sane).
- Verify VVIX + ^VIX snapshots flowing (enrolled 7/11); vol_regime
  auto-prefers spot ^VIX over VX_F once data exists (VOL line still shows
  6 sleeves, no VIX-spot/VVIX yet).
- Monday 9:31 ET: first REAL REPORT NOW (tonight's renders = Friday
  closes, EDGES empty by construction).

## FIRST TASKS NEXT SESSION (ENTIRE SPRINT P1+P2+P3 SHIPPED 7/11-12)
0. Post-deploy checks above first (+ KEITH · KEITH WEEKLY from phone).
1. **Keith add-pattern detector — SHIPPED 7/12** (tools/keith_pattern.py):
   state machine ENTRY(trend->BULL transition; loose mode accepts standing
   bull while history is shallow) -> ARMED(rp>=.55) -> PULLED(rp<=.35) ->
   TESTED(rp<=.15) -> HOLD(closed up, trend intact) = setup. Support = RR
   buy_trade where in that day's RR email, else MFR range low. BACKTEST
   VERDICT (7/12): UNVALIDATED — evaluable PS adds were Quad-4 macro-ETF
   rotation (wrong sample for the pattern), SS corpus a week old. So: NO
   auto per-setup alerts; KEITH/KEITH STRICT/KEITH WEEKLY commands +
   hardwired Friday-EOD weekly report (fired setups + PS 7d / SS 14d
   recall counters, stored kind='keith_weekly') — paper-trade record
   builds itself; revisit wiring live alerts when recall proves out.
   Re-run _keith_backtest.py weekly as corpora deepen. Operator insights
   captured: SS shows the pattern mid-breakout (setups should LEAD SS
   adds); SS drops = invalidation tell (future: reset brewing on SS drop);
   futures in the list stay (country-ETF adds — FCE_F≈EWQ, FESX_F≈FEZ;
   formalize via wrapper-links later); ONCE LONG SIDE VALIDATES, mirror
   for SHORTS (bearish entry -> rally -> REJECTED at trade resistance ->
   closes down; validate vs keiths_signals shorts + etfpro short bias) —
   parameter flip, same machine.
2. **Vision-OCR image ingest — BUILT 7/11 night** (live need: Tier One
   Alpha arrives as screenshots). photo -> getFile -> Claude vision
   transcription (extraction only, OCR_MODEL in doc_ingest) -> classify/
   store; buffer-aware (DOC START -> photos -> DOC END = one stitched
   row). VERIFY live next session; the deferred SS-PNG anchor can now
   reuse ocr_image() directly.
3. **tier1alpha deep parse** (on top of doc_uploads rows): 1M vs 3M vol,
   CTA buy/sell, risk-on/off -> actionable regime flags (e.g. 1M<3M ->
   vol-control funds set up to buy).
3. Then the standing queue (unchanged, renumbered below).

ORIGINAL P1 SPEC (shipped — kept for reference):
1. **REPORT NOW** (intraday Telegram command, companion to EOD/AM report):
   - Universe: FULL fractal range (MFR) DB — NOT just the SS list.
   - Filter: sector/style + factor-exposure compliant AND has defined
     signal strength.
   - Rotation cues FROM the EOD report's sector flow (money out of XLU,
     into XLK/XLY): hot sectors -> surface LONG candidates; cooling
     sectors -> surface SHORT candidates from those sectors. (The ✓/✗
     flow-quality marks shipped today are the input.)
   - Show ALL compliant candidates even if at cap — override is operator
     discretion. Flag, never hide.
   - Compliance PER BOOK (IND / Rollover / Roth), not whole AUM — the
     per-account fill machinery from TRANCHE v2 is the substrate.
   - Sizing baked in: starter 100 bps; adds 50/100 bps increments; ETF cap
     4% in report (6% actual ceiling — flag, don't hide); shorts HARD 2%
     max, 50 bps starter — flag breaches, still surface.
   - Output compact + LLM-friendly, single upload, smaller than EOD.
2. **SpotGamma manual upload** (Telegram ingest): KILL the 5K equity
   scanner scrape (2 weeks stuck, not worth it). Build file-upload path:
   download report on phone -> send to bot -> ingest. Parse founder's note
   -> flag/store for future RAG layer. Headless tape canary (15-min)
   stays as-is — working. (sendDocument plumbing shipped today; ingest
   direction = receiving documents, needs the getFile path.)
3. **Keith add-pattern detector** (float mid-week): bullish TREND entry ->
   sells off -> holds TRADE support -> Keith adds. Detect that sequence
   across bot inventory to front-run Portfolio Solutions adds. BOT layer
   (Python-computed), not RAG.
4. Then the standing queue: native macro symbols (KRIS CONFIRM per mapping;
   GOLD = Barrick trap) · full-universe tagging / quad-doctrine table /
   style factors (also shrinks 36 GUESSED tiers + CONC untagged 70%) ·
   BTC Quant deepening · Databento · ML layer. Deferred: outcomes v3
   expiry handling · SS PNG Vision-OCR anchor. QUAD WATCH stays deferred
   until the quad-doctrine table exists (no hardcoded quad maps).

## SEPARATE TRACKS (not bot code)
- **Claude RAG project** ("Keith/Hedgeye/SpotGamma brain"): new Claude
  Project, separate from the bot. Corpus: Macro Show, Early Look, SpotGamma
  AM/PM notes. Use: upload REPORT NOW output -> in-game decision support.
- **Tier One Alpha** (placement TBD, likely BOTH): Telegram ingest into bot
  (actionable regime flags — e.g. 1M vol < 3M vol -> vol-control funds set
  up to buy; CTA buy/sell, risk-on/off) + RAG corpus (macro framing).
- Standing: build heavy stuff NOW while Fable access is cheap/free.
  Weekend = paid work or bot build only.
- Repos push: DONE 7/11 pm — bot AND canary verified on GitHub (old
  "local-only" note was stale).

## DATED
- Mon 7/13: Feed 2 verdict (PM anchor vs week of dry-run deltas).
- ~week of 7/13: review 20/80 suppression counts before 15/85 decision.
- ~Sat 7/18: REPORT LEGACY parallel week ends — remove v3 renderer if v4
  held up.
- Jul 17: option expiries — book holds ZERO options; if he re-enters,
  expiry-watch build becomes urgent.
- Telegram pending: WRAP NO DRIP if not yet answered (EXP match is a
  description misparse — NOT Eagle Materials; real underlying ≈ XOP inv 2x).
  NOTE: DRIP currently defaults dflt-sat 1% which is right either way.

## TEST FILES (run before any commit touching these areas)
test_book_direction.py · test_rta_cross_signal.py · test_book_alerts.py ·
test_report_v4.py · test_position_targets.py
Diagnostics: _book_sides_verify.py · _wrapper_flag_audit.py · _desc_audit.py
· _etfpro_health_scan.py · _options_postmortem.py (Kris hasn't run it yet) ·
_last_upload_dates.py · _daily_upload.py (evening routine, ONE command)
Apply scripts: _apply_063.py = superseding pattern reference (migration +
SIMULATED dry run + --commit seeds). _apply_062.py is a stub, ignore.
