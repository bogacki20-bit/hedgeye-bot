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
| 7:15–9:00 | Email ingest alive. SS roster matches the latest emails. New Financials/Retail/CapAlloc calls in `data/stalk/ledger.csv`. Fill earnings from the morning mail. Monday: Position Monitor capture + dry-run, then ASK before `--commit`. | Kris has the changes |
| 9:00 | Book calls by account, OUT → IN, each tagged with its rule and trigger price. Net fills and `book_activity` first. | Calls sent |
| 9:00 | BUXX: check the last ex-dividend date. Within 7 days → tell Kris the window, price, rp and pace; flag it the first day. Don't rely on the `BUXX Accumulator 0945` task alone (it never sent an alert in Sept or Oct). | Kris told, or no window open |
| 9:32 | Read the SpotGamma Founder's Note. Flag agreement or conflict with Hedgeye/T1A/the book; log its calls. | Note summarized |
| 10:00, 12:30, 3:00 | Check-in: `desk_rot`, `desk_shorts`, `desk_sizing`. Send market read, SS changes, then a ranked "execute next" list across SS shorts and longs with rule, trigger price and earnings gate. | Check-in sent |
| 3:45 | Call trade/trend breaks still through the line. | Breaks sent, or "none" |
| After 4:00 | Re-run the scans on closing prices. | Close read sent |
| Evening | The Call + Macro Show transcripts (links expire ~1 h), process notes. Confirm the 6:45 PM daily pack built. | Files sent |

## Every call
- Stamp intraday reads "as of HH:MM".
- Know the tool traps: `desk-accuracy-checklist` item 10.
- Trade = 34-day EMA, Trend = 89-day EMA on every position (Kris locked 11/2). Keith's published number wins; 2% buffer on EMA-only breaks.
- Research: task `research-digest-daily` loads new reports at 11:45 AM and 7:45 PM. Before any call on a name, run `python research_digest.py --lookup NAME` and use it; `desk_rot` shows it for held and stalk names. If the task did not run, digest by hand.
- No buys within 2 days of earnings; the tools have no gate, so check by hand.

## Ask Kris first
Position Monitor `--commit`, code changes, commit/push/deploy.
