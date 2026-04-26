# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hedgeye Bot — a Python service that monitors Hedgeye Risk Management content (portal + email), classifies it with Claude AI, and sends push notifications via Pushover for trade signals.

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

Two daemon threads run concurrently from `main.py`:

**Scraper thread** (`scraper.py`) — Playwright (headless Chromium) logs into `app.hedgeye.com` with email/password, polls the feed every 15 minutes, fetches full article content for new items, then classifies each one. Anti-automation flags (`--disable-blink-features=AutomationControlled`) and a `navigator.webdriver` override are applied to reduce reCAPTCHA friction. If a reCAPTCHA challenge is detected, the bot sends a Pushover alert and stops.

**Email thread** (`email_parser.py`) — Connects to iCloud IMAP (`imap.mail.me.com:993`) using an app-specific password, polls every 15 minutes for unread messages from `hedgeye.com` / `tier1alpha.com`, parses and classifies each one.

Both threads share the same pipeline:
```
raw item dict → classify_and_extract() → save_item() → send_notification() [if high-conviction]
```

**Classifier** (`classifier.py`) — Sends content to `claude-sonnet-4-20250514` with a strict JSON-only system prompt. Returns a structured dict with `classified_type`, `tickers[]`, `conviction`, `macro_regime`, `spx_levels`, etc. The full model response is merged directly into the item dict.

**Database** (`database.py`) — SQLite at `DB_PATH` (default `/data/hedgeye.db`). Three tables: `items` (all content), `signals` (per-ticker rows extracted from items), `morning_briefs` (deduplication for daily brief). `init_db()` runs on import.

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

## Common modifications

**Change conviction filter for immediate alerts** — `scraper.py`: `item.get("conviction") in ("Best Idea", "Adding")`. Remove `"Adding"` to reduce noise.

**Add a new Hedgeye sender domain** — `email_parser.py`: add to `HEDGEYE_SENDERS` list and the `conn.search()` loop in `fetch_new_hedgeye_emails`.

**Scraper breaks after Hedgeye HTML changes** — Update CSS selectors in `scrape_feed()` and `fetch_full_content()` in `scraper.py`.
