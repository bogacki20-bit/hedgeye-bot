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
| First touch | RC, keep-awake, heartbeat. tv_levels older than ~20 h → run the capture, no ask (fresh pairs, book + macro first). Monday: hedgeye_quad older than 7 days → read the dashboard. | Levels fresh, heartbeat running |
| By 7:30 | Read the brief. Check every Action against the rule card and send Kris the conflicts. | Conflicts sent |
| 7:15–9:00 | Email ingest alive. SS roster matches the latest emails. New Financials/Retail/CapAlloc calls in `data/stalk/ledger.csv`. Fill earnings from the morning mail. Monday: Position Monitor capture, dry-run and `--commit` without asking (Kris 11/2); ask only if it looks wrong. | Kris has the changes |
| By 8:30, after close | Research: `py research_digest.py --pending 7`, digest what it lists, then `py -m tools.research_tilt`. Send Kris what's against the book, new ideas with 2+ sources, global quad changes and turns, and any process change the research argues for, as a proposal. Only Kris changes a rule. | Tilt sent |
| Mon, and any quad check | `/macro_dashboard`: header quads to the bot, then click "Global Quad", save the table to `data/hedgeye/quad_countries/<date>.txt`, `py -m tools.country_quads --load <file>`. Flag path changes to Kris. | Read stored |
| 9:00 | Book calls by account, OUT → IN, each tagged with its rule and trigger price. Net fills and `book_activity` first. | Calls sent |
| 9:00 | BUXX: check the last ex-dividend date. Within 7 days → tell Kris the window, price, rp and pace; flag it the first day. Don't rely on the `BUXX Accumulator 0945` task alone (it never sent an alert in Sept or Oct). | Kris told, or no window open |
| 9:32 | Read the SpotGamma Founder's Note. Flag agreement or conflict with Hedgeye/T1A/the book; log its calls. | Note summarized |
| 10:00, 12:30, 3:00 | Check-in: `desk_rot`, `desk_shorts`, `desk_sizing`, `research_tilt`, `formations`, `stalk`. Send market read, SS changes, then a ranked "execute next" list across SS shorts and longs with rule, trigger price and earnings gate. | Check-in sent |
| 3:45 | Call trade/trend breaks still through the line. | Breaks sent, or "none" |
| After 4:00 | Re-run the scans on closing prices. | Close read sent |
| Evening | The Call + Macro Show transcripts (links expire ~1 h), process notes. Confirm the 6:45 PM daily pack built. | Files sent |

## Every call
- Read `docs/playbook/RULE_CARD.md` and memory `desk-accuracy-checklist` at first touch and after every compaction, before the first call.
- Stamp intraday reads "as of HH:MM".
- Know the tool traps: `desk-accuracy-checklist` item 10.
- Trade = Vola2vola, Trend = 89-day EMA on every position (Kris 11/5, 11/2). Keith's published number wins; 2% buffer on our own lines.
- Full reports: task `hedgeye-report-capture` pulls the portal pages and PDFs behind the emails through Kris's signed-in Chrome (7:30, 12:30, 6:30 Mon-Sat) into `data/hedgeye/research/<date>/`. At each check-in, read the new rows at the end of `_capture_log.csv` there, and read the decks (`.txt`, plus `_pN.png` for chart pages). `py -m tools.report_capture status` shows what is left.
- Research: task `research-digest-daily` is meant to load new reports at 11:45 AM and 7:45 PM, but app tasks miss, so check `--pending 7` yourself. ETF Pro update emails and Real-Time Alerts load on their own. Before any call on a name, run `python research_digest.py --lookup NAME` and use it; `desk_rot` shows it for held and stalk names. If the task did not run, digest by hand.
- No buys within 2 days of earnings; the tools have no gate, so check by hand.

## Ask Kris first
Code changes, commit/push/deploy.
