# Dry-Run Framework — Implementation Plan

**Status:** schema migration is written and ready (`migrations/002_dry_run_framework.sql`). Code changes to `db_pg.py`, `price_monitor.py`, `telegram_handler.py` will be applied via the command bridge once it's installed tonight, in small focused commits to avoid the file-truncation issue.

## Tonight's deploy sequence

### 1. Reset the corrupted files

```powershell
cd C:\Projects\hedgeye-bot
git status
git checkout -- schema.sql db_pg.py price_monitor.py telegram_handler.py
git status
```

After this, only `migrations/002_dry_run_framework.sql`, `command_bridge.py`, `SETUP_COMMAND_BRIDGE.md`, and the data/ snapshot files should show as new/untracked. The four corrupted edits are reverted.

### 2. Install the command bridge per `SETUP_COMMAND_BRIDGE.md`

(Railway CLI install → bridge daemon smoke test → Windows Task Scheduler auto-start)

### 3. Apply the schema migration to Railway Postgres

Either via Railway CLI from the laptop:

```powershell
railway run psql $env:DATABASE_URL -f migrations/002_dry_run_framework.sql
```

Or once the bridge is up, ask me to do it via a `python_script` bridge command. Either way takes ~10 seconds.

### 4. Commit the safe parts that survived

```powershell
git add migrations/ command_bridge.py SETUP_COMMAND_BRIDGE.md data/
git commit -m "Add command bridge daemon and dry-run framework schema migration" -m "command_bridge.py polls .commands/ for whitelisted git/Railway/Telegram/python ops; SETUP_COMMAND_BRIDGE.md install guide; migrations/002_dry_run_framework.sql adds user_actions and outcome_followups plus extends alerts_fired with recommendation context. Code wiring (price_monitor + telegram_handler) follows in a separate commit once bridge is operational."
git push
```

Railway redeploys with the migration files in the repo (but the bot doesn't yet *use* the new tables — that's the next step).

### 5. Wire the code changes via the bridge

Once the bridge is operational, I issue these edits as bridge commands one at a time — each gets verified before the next. Three files to edit, four targeted edits total:

**Edit A — `db_pg.py`:** extend `record_alert(...)` signature with the new optional kwargs (`recommendation_text`, `suggested_action`, `suggested_dollars`, `framework_alignment`, `hedgeye_context`, `spotgamma_context`) and pass them in the INSERT.

**Edit B — `db_pg.py`:** add three new helper functions: `find_alert_by_id`, `find_recent_alert_for_ticker`, `save_user_action`.

**Edit C — `price_monitor.py`:** add `compose_recommendation()` function and update `run_monitor_cycle` to call it, then pass the recommendation into both `record_alert` (capturing context) and `format_alert_message` (so Telegram includes it). Also flip the order of operations: record alert FIRST to get the alert_id, then format the Telegram message with `[A{id}]` prefix, then send.

**Edit D — `telegram_handler.py`:** rewrite the listener to parse user replies for alert IDs (`A\d+` pattern) and decision keywords (approve / skip / bought / sold / etc.), then save to `user_actions`. Reply syntax documented in the new docstring.

Each edit gets a syntax-check via the bridge before moving to the next. After all four land, Railway redeploys, and the dry-run framework is live.

## The fact pattern dry run will test

When a price_monitor alert fires (next NFP volatility likely Friday open), the Telegram message looks like:

```
🟢 [A1234] HYG — BUY ZONE — bottom third
price 79.50
buy 79.67 — sell 80.22
0% of range
prev close 79.80, trend BULLISH
signal 2026-05-08

SCALE-IN candidate. Bottom third of Hedgeye range, trend=BULLISH.
Suggested: starter $300 (Style B 100bps approx) up to $500 on conviction. $1K real-world ceiling applies.

Reply with `A1234` + your decision:
`A1234 approve` / `A1234 skip` / `A1234 bought $X @ Y`
```

You see it on phone, decide, reply something like `A1234 bought $400 @ 79.45` (or `A1234 skip`). The bot parses your reply, writes a `user_actions` row with executed=true / executed_action=BUY / dollars=400 / price=79.45 (or executed=false / decision=rejected). Daily corpus accumulates real fact-pattern data: which alerts you took, which you skipped, what you executed at, and (when outcomes_log job runs) what played out 1d/5d/20d later.

That's the test. After 2-3 days of real alerts and real replies, we look at the corpus and see if the pattern is reasonable, the recommendations are useful, the parser captures replies cleanly. Adjust from there.

## What a "doesn't work" finding would look like

- Replies don't get parsed correctly — pattern needs more verbs / formats
- Recommendations don't actually feel actionable — sizing logic too generic without portfolio context
- You never reply because typing the alert ID is friction — switch to button-based Telegram inline keyboards
- Alerts fire too often / not at right zones — tune the `compute_zone` thresholds

Each is a known-shape problem with a clear fix. None requires a different architecture.
