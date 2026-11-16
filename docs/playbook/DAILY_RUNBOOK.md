# Daily desk runbook (ET, market days)

You run this without being asked. Kris should not have to prompt any step.
Calls follow `docs/playbook/RULE_CARD.md`. Recommend only, never trade. Lead ≤ 4 lines, no disclaimers.

## How to stay on schedule
A chat only acts when something wakes it. Keep a background sleep running all day, timed to land on the next duty below (at most 1500 s).
On every wake: `get_session("self")`, turn Remote Control on if it's off, `request_keep_awake(session_idle)`, compare the clock to this list, do what's due, re-arm the sleep.
Never end a turn during market hours without a sleep running.

## Schedule
| Time | Duty | Done when |
|---|---|---|
| First touch | RC, keep-awake, heartbeat. tv_levels older than ~20 h → run the capture, no ask (fresh pairs, book + macro first). Monday: hedgeye_quad older than 7 days → read the dashboard. **Feeds (11/10): check the DATA, not the task status** — a task can say "succeeded" and store nothing (T1A stored nothing 10/27–11/10), and one hung run blocks a task for days (10/27→11/6). Run `py -m tools.feed_health` (every paid feed: T1A, deck, transcripts, report backlog, SpotGamma, TV, book) at first touch and every check-in. Anything STALE → run it by hand now and tell Kris. After each check-in: `py -m tools.desk_snapshot --raw` then `py -m tools.drive_export` (Google Drive folder for Gemini/Claude project/OpenClaw). | Levels fresh, feeds fresh, heartbeat running |
| By 7:30 | Read the brief. Check every Action against the rule card and send Kris the conflicts. | Conflicts sent |
| 7:15–9:00 | Email ingest alive. SS roster matches the latest emails. New Financials/Retail/CapAlloc calls in `data/stalk/ledger.csv`. Fill earnings from the morning mail. Monday: Position Monitor capture, dry-run and `--commit` without asking (Kris 11/2); ask only if it looks wrong. | Kris has the changes |
| By 8:30, after close | Research: `py research_digest.py --pending 7`, digest what it lists, then `py -m tools.research_tilt`. Send Kris what's against the book, new ideas with 2+ sources, global quad changes and turns, and any process change the research argues for, as a proposal. Only Kris changes a rule. | Tilt sent |
| Mon, and any quad check | `/macro_dashboard`: header quads to the bot, then click "Global Quad", save the table to `data/hedgeye/quad_countries/<date>.txt`, `py -m tools.country_quads --load <file>`. Flag path changes to Kris. | Read stored |
| 9:00 | Book calls by account, OUT → IN, each tagged with its rule and trigger price. Net fills and `book_activity` first. | Calls sent |
| 9:00 | BUXX: check the last ex-dividend date. Within 7 days → tell Kris the window, price, rp and pace; flag it the first day. Don't rely on the `BUXX Accumulator 0945` task alone (it never sent an alert in Sept or Oct). | Kris told, or no window open |
| 9:32 | Read the SpotGamma Founder's Note. Flag agreement or conflict with Hedgeye/T1A/the book; log its calls. | Note summarized |
| Every check-in (7:15, 9:32, :02/:32) | Research reader first: a fresh Agent helper runs docs/playbook/RESEARCH_READER.md (reads every new report in full, loads research_digest, returns 12 lines or fewer). The desk never reads full reports into its own chat. | Reader lines in the check-in |
| Every check-in and glance (9:32, :02/:32, 3:45) | SpotGamma tape: `py -m tools.sg_tape --read --window 30` (the capture desk reads it every 15 min). Tape line in every full check-in, one tape line in every glance. Context for the picture's "who's buying"; it never overrides a line or range. | Tape line sent |
| Before any action list | Rule checker: a fresh Agent helper runs docs/playbook/RULE_CHECKER.md on the proposed lines (reads the card fresh, verifies numbers, returns ALL PASS / FIX / MISSING). Re-read RULE_CARD.md in full at 9:32, 12:32, 3:45 and after every compaction. | "Checked HH:MM" line on the list |
| 10:00, 12:30, 3:00 | Check-in: `rule_scanner --dry-run` first, then `desk_rot`, `desk_shorts`, `desk_sizing`, `research_tilt`, `formations`, `stalk`, `factor_board --hud` (top/bottom 3 factors + sectors = rotate from → to). Every name gets a one-line what-it-is/driver. Send market read, SS changes, then a ranked "execute next" list across SS shorts and longs with rule, trigger price and earnings gate. | Check-in sent |
| 3:45 | Call trade/trend breaks still through the line. | Breaks sent, or "none" |
| After 4:00 | Re-run the scans on closing prices. | Close read sent |
| Evening | The Call + Macro Show transcripts (links expire ~1 h), process notes. Confirm the 6:45 PM daily pack built. | Files sent |

## Every call
- Read `docs/playbook/RULE_CARD.md` and memory `desk-accuracy-checklist` at first touch and after every compaction, before the first call.
- Stamp intraday reads "as of HH:MM".
- Know the tool traps: `desk-accuracy-checklist` item 10.
- Trade = Vola2vola (34 EMA fallback). Trend = the yellow MFR cloud: bottom edge for longs, top edge for shorts (Kris 11/9); 89 EMA only when a name has no cloud. Keith's published number wins; 2% buffer on Vola/EMA lines only. RULE_CARD.md is the source of truth if this line ever disagrees.
- Every check-in starts with `py -m tools.rule_scanner --dry-run` (book + SS + PM + ETF Pro + watch list). Post ONE combined list: scanner fires + last night's day-3 list + watch list + ETF Pro shorts rp ≥ .65. Never a call from a partial screen (11/10 misses: PBD, FORM, RDDT).
- Full reports: task `hedgeye-report-capture` pulls the portal pages and PDFs behind the emails through Kris's signed-in Chrome (7:30, 12:30, 6:30 Mon-Sat) into `data/hedgeye/research/<date>/`. At each check-in, read the new rows at the end of `_capture_log.csv` there, and read the decks (`.txt`, plus `_pN.png` for chart pages). `py -m tools.report_capture status` shows what is left.
- Research: task `research-digest-daily` is meant to load new reports at 11:45 AM and 7:45 PM, but app tasks miss, so check `--pending 7` yourself. ETF Pro update emails and Real-Time Alerts load on their own. Before any call on a name, run `python research_digest.py --lookup NAME` and use it; `desk_rot` shows it for held and stalk names. If the task did not run, digest by hand.
- No buys within 2 days of earnings; the tools have no gate, so check by hand.

## Ask Kris first
Code changes, commit/push/deploy.
