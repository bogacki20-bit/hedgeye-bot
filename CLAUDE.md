# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hedgeye Bot — a Python service that watches the user's iCloud inbox for emails from Hedgeye Risk Management, classifies them with Claude AI, and (for trade signals) produces a sized trade **recommendation** against the user's three Fidelity accounts. Recommendations are pushed to the user's phone via Pushover for manual approval. The bot does not execute trades.

## Running locally

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Set up environment
cp .env.example .env
# Fill in all values in .env

# Run the bot
python main.py

# Run either component standalone
python scraper.py
python email_parser.py
```

## Deployment (Railway)

```bash
railway login
railway up
```

Set all `.env` values in the Railway dashboard under **Variables → Raw Editor**. Add a volume mounted at `/data` for SQLite persistence. The start command in `railway.toml` installs Chromium before launching.

## Architecture

`main.py` runs the email parser in the foreground. The scraper file (`scraper.py`) is currently dormant — reCAPTCHA blocks reliable headless login. Pipeline per email:

```
IMAP fetch → Pushover (raw email) → classify_and_extract() → save_item()
                                          ↓
                                  if trade_signal:
                                  recommend_from_signal() → Pushover (recommendation)
                                                          → trade_recommendations row
```

**Email parser** (`email_parser.py`) — iCloud IMAP (`imap.mail.me.com:993`) with an app-specific password. Polls every 15 minutes; uses `SINCE` (last 2 days) so emails read in a mail client are still processed; deduplication via DB `seen_ids`. Searches `FROM "hedgeye"` to catch every subdomain.

**Classifier** (`classifier.py`) — Sends email content to `claude-sonnet-4-20250514` with a strict JSON-only system prompt; returns `classified_type`, `tickers[]`, `conviction`, `macro_regime`, `spx_levels`, etc.

**Portfolio** (`portfolio.py`) — Ingests Fidelity CSV exports (`Portfolio_Positions_*`, `History_for_Account_*`, `Accounts_History*`) into `portfolio_positions` and `portfolio_transactions`. Encodes the three-account rule set: Individual (`X96383748`) can long+short; Rollover IRA (`244859926`) and Roth IRA (`245734604`) are long-only; no options anywhere; the Individual account preserves a $5,000 margin buffer. Exposes `get_position_for()`, `position_summary()`, `account_value()`, `can_trade()`, `hedgeye_target_account()`.

**Recommender** (`recommender.py`) — Given a classified `trade_signal`, picks the target account, sizes by conviction (`Best Idea` 5%/$2.5k cap, `Adding` 3%/$1.5k cap, `Reducing` -50%, `Remove` -100%), respects the margin buffer, writes a `trade_recommendations` row, and returns a Pushover-ready summary.

**Database** (`database.py`) — SQLite at `DB_PATH` (default `/data/hedgeye.db`). Tables: `items`, `signals`, `morning_briefs`, `portfolio_positions`, `portfolio_transactions`, `trade_recommendations`. `init_db()` runs on import.

**Notifier** (`notifier.py`) — Pushover via stdlib `urllib`; truncates at 1024 chars.

## Alert logic

- **Immediate push notification**: any `trade_signal` with conviction `"Best Idea"` or `"Adding"` triggers a Pushover notification right away (from scraper) or when `action_required=True` (from email).
- **Morning brief**: sent once per day at `MORNING_BRIEF_HOUR` (default 7am local time), accumulating overnight items. Deduplication tracked in `morning_briefs` table.

## Key env vars

| Variable | Purpose |
|---|---|
| `HEDGEYE_EMAIL` / `HEDGEYE_PASSWORD` | Portal login credentials |
| `ICLOUD_EMAIL` / `ICLOUD_APP_PASSWORD` | IMAP (app-specific password, not your Apple ID password) |
| `ANTHROPIC_API_KEY` | Claude API |
| `PUSHOVER_TOKEN` | Pushover application API token |
| `PUSHOVER_USER` | Pushover user key |
| `DB_PATH` | SQLite path (default `/data/hedgeye.db`) |
| `SCRAPE_INTERVAL_SECONDS` | Portal poll interval (default `900`) |
| `EMAIL_CHECK_INTERVAL` | Email poll interval (default `900`) |
| `MORNING_BRIEF_HOUR` | Hour to send brief (default `7`) |

## Ingesting Fidelity CSVs

```bash
python portfolio.py ~/Downloads/Portfolio_Positions_Apr-12-2026.csv
python portfolio.py ~/Downloads/                 # ingests every recognized file in dir
```

Re-ingesting a `Portfolio_Positions_*.csv` for the same snapshot date is idempotent (existing rows for that date are replaced). Transaction files dedupe on `row_hash`.

## Account & sizing rules

Defined as constants — change here, the recommender follows automatically.

| File | Constant | Purpose |
|---|---|---|
| `portfolio.py` | `ACCOUNTS` | Per-account flags: `long`, `short`, `options`, `etfs_only`, `margin_buffer`, `hedgeye_target` |
| `portfolio.py` | `MARGIN_BUFFER_USD` | Borrowing-capacity reserve on the Individual account ($5,000) |
| `recommender.py` | `SIZING` | % of account + dollar cap per conviction tier |

To enable options later: flip `options: True` in `ACCOUNTS[<acct>]` and loosen the `instrument` check in `can_trade()`.

## Common modifications

**Change recommended trade size** — `recommender.py` `SIZING` dict.

**Add a new Hedgeye sender domain** — `email_parser.py`: add a keyword to the loop in `fetch_new_hedgeye_emails`.

**Route Hedgeye signals to a different account by default** — `portfolio.py` `hedgeye_target_account()`.
