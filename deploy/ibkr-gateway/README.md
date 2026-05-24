# ibkr-gateway — Railway service setup

Second Railway service alongside the bot. Runs our own Selenium + Flask
wrapper around IBKR's Client Portal Web API login flow. The bot reaches
it on the Railway private network at
`http://ibkr-gateway.railway.internal:5000`.

## Why we replaced ibeam

We previously ran `voyz/ibeam` here. Both 0.5.11 (2026-04-09) and 0.5.12
(2026-04-21) ship with post-login selectors (`#twofactbase`,
`#xyz-field-bronze-response`, `.ibkey-promo-skip`, `.xyz-errormessage`)
that no longer match anything on the current IBKR login UI. Upstream
fixes look stalled. Rather than fork ibeam, we wrote a minimal
replacement so the selectors live in one obvious file (`auth.py`,
`SELECTORS` dict) that we control.

The previous ibeam-based Dockerfile is preserved at
`legacy/Dockerfile.ibeam` for rollback.

## Architecture

```
app.py        Flask app, serves :5000
  └─ session.py    in-memory cookie jar + tickle/reauth daemons
       └─ auth.py    Selenium login flow (SELECTORS dict lives here)
```

Endpoints exposed (mirroring IBKR's Client Portal API URLs so the bot's
`tools/price_feed_ibkr.py` is unchanged):

- `GET /v1/api/iserver/auth/status` — auth state. Returns 200 even before
  initial login completes so Railway healthcheck passes during cold boot.
- `GET /v1/api/iserver/secdef/search?symbol=SPY` — conid lookup, proxied.
- `GET /v1/api/iserver/marketdata/snapshot?conids=...&fields=...` — quote,
  proxied.
- `GET /v1/api/<anything>` — catch-all passthrough proxy.
- `GET /_ping` — plain-text liveness for hand-curling.

Auth runs in a background thread on startup. Two more daemon threads:
- tickle every 60s to prevent idle timeout
- full re-auth every 23h, ahead of IBKR's daily maintenance window

## Updating selectors (the part that breaks)

The four `TODO_` placeholders in `auth.py`'s `SELECTORS` dict MUST be
filled in before login will work. The service will start (and answer
healthchecks) with stubs in place, but `authenticated` will stay false
because `auth.login()` refuses to proceed when stubs are present and
records the error in `_last_error`.

To update them:

1. Open the live IBKR login page in a real Chrome:
   `https://www.interactivebrokers.com/sso/Login?forwardTo=22&RL=1&ip2loc=on`
2. DevTools → inspect each element:
   - The login submit button → `SUBMIT`
   - After hitting submit, the 2FA code input field → `TWO_FA_INPUT`
   - The 2FA submit button → `TWO_FA_SUBMIT`
   - The post-auth dashboard element OR URL substring that confirms you're
     logged in → `SUCCESS_INDICATOR` (you can also rely on
     `SUCCESS_URL_SUBSTRINGS` if no clean element exists)
   - The inline error message that shows on bad credentials → `ERROR`
     (optional, for cleaner failure logs)
   - The "switch to IBKey" upsell skip button if it appears →
     `IBKEY_PROMO_SKIP` (optional, skipped silently if not found)
3. Prefer stable selectors — `id`, `name`, `data-*` attrs over deeply
   nested CSS paths.
4. Replace each `(By.CSS_SELECTOR, "TODO_...")` tuple with the real
   `By, value` pair. Commit + redeploy.

## One-time setup

1. **Create the service.** In the Railway project dashboard:
   - **New → Empty Service** (or `railway service create ibkr-gateway`).
   - **Settings → Source → Connect Repo:** point at this repo.
   - **Settings → Source → Root Directory:** `deploy/ibkr-gateway`.
   - **Settings → Networking:** leave public networking OFF.

2. **Environment variables.** Add these in the new service's **Variables**
   tab. None of them belong in any file in the repo:

   | Variable | Value | Notes |
   |---|---|---|
   | `IBEAM_ACCOUNT` | your IB username | live or paper |
   | `IBEAM_PASSWORD` | your IB password | plaintext (operator decision: no Fernet) |
   | `IBEAM_TOTP_SECRET` | TOTP base32 secret | omit for paper accts w/o 2FA |

   We kept the `IBEAM_*` variable names so swapping the Railway service
   between ibeam and our custom gateway doesn't require renaming vars.
   `IBEAM_KEY` and `IBEAM_TWO_FA_HANDLER` from the old ibeam config are
   no longer used and can be removed.

   Optional advanced overrides (rarely touched):

   | Variable | Default | Purpose |
   |---|---|---|
   | `IBKR_LOGIN_URL` | `https://www.interactivebrokers.com/sso/Login?...` | login entry point |
   | `IBKR_API_BASE` | `https://www.interactivebrokers.com` | REST proxy target |
   | `IBKR_TICKLE_INTERVAL` | `60` | seconds between tickle calls |
   | `IBKR_REAUTH_INTERVAL` | `82800` (23h) | seconds between full re-logins |
   | `IBKR_LOGIN_RETRY_DELAY` | `300` | seconds between failed login retries |

3. **Deploy.** Push the `ibkr-custom-gateway` branch (or merge it after
   the selectors are filled in). Railway picks up the new image on
   the next build. First boot is ~30-60s — pip install + chromium runtime
   (we don't fetch IB Gateway anymore, that whole layer is gone).

4. **Verify.** From a shell with access to the Railway project, exec into
   the bot service and run:
   ```
   python -m tools.price_feed_ibkr --healthcheck SPY
   ```
   - `ibeam gateway authenticated: False` immediately at deploy time is
     expected — auth runs async. Wait a minute and retry.
   - `True` means the selectors are correct and creds are valid.
   - `False` after a few minutes + `_last_error` in the auth/status JSON
     telling you which selector failed = update the SELECTORS dict.
   - `None` = service unreachable (the Flask app didn't start; check
     `railway logs --service ibkr-gateway`).

5. **Flip the bot to IBKR** (only after the gateway authenticates):
   ```
   PRICE_FEED=ibkr
   IBKR_GATEWAY_URL=http://ibkr-gateway.railway.internal:5000
   ```

## Daily operations

- The tickle thread keeps the session alive every 60s.
- The reauth thread does a full Selenium re-login every 23h.
- Container restart wipes the cookie jar and triggers a fresh login.
  Bot falls back to yfinance during the ~1 min auth window.

If the gateway flaps:
- Bot stays up. `tools/price_feed_ibkr.get_quote()` returns None, the
  dispatcher in `yfinance_client.fetch_raw` falls back to yfinance.
- `railway logs --service ibkr-gateway` for our Flask logs + the
  selenium login thread.
- `curl http://ibkr-gateway.railway.internal:5000/v1/api/iserver/auth/status`
  shows `_last_error` if login failed and `_placeholder_selectors` if any
  selectors are still TODOs.
- Restart the gateway service in the Railway dashboard to force a
  fresh login.

## Architectural caveat — REST proxy target

This service assumes the cookies captured from
`https://www.interactivebrokers.com/sso/Login` are valid against the
`/v1/api/iserver/*` endpoints at that same domain. If that turns out not
to be the case (those endpoints have historically been served by the
locally-hosted `clientportal.gw` Java app), the fallback is:

1. Add `clientportal.gw` (the Java app from IBKR's downloads page) to
   the Dockerfile and start it on `localhost:5000` inside the container.
2. Set `IBKR_LOGIN_URL=https://localhost:5000/sso/Login?forwardTo=22&RL=1`
   and `IBKR_API_BASE=https://localhost:5000`.
3. Bump the Flask listen port off 5000 (e.g. 5001) and update the
   Dockerfile `EXPOSE` + Railway healthcheck path / `IBKR_GATEWAY_URL`.

This was the architecture ibeam used. We're trying the simpler
direct-IBKR approach first because it removes the JVM cold-boot from the
boot time. If snapshots come back empty or 4xx after auth, switch.

## Conid cache

Unchanged from the ibeam setup — see `migrations/029_ibkr_conid_cache.sql`.
