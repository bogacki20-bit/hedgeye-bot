# ibkr-gateway — Railway service setup

Second Railway service alongside the bot. Runs the `voyz/ibeam` Docker
image, which wraps IB Gateway + the Client Portal Web API with auto-login
and 2FA handling. The bot reaches it on the Railway private network at
`http://ibkr-gateway.railway.internal:5000`.

## One-time setup

1. **Create the service.** In the Railway project dashboard:
   - **New → Empty Service** (or via CLI: `railway service create ibkr-gateway`).
   - **Settings → Source → Connect Repo:** point at this repo
     (`bogacki20-bit/hedgeye-bot`).
   - **Settings → Source → Root Directory:** set to `deploy/ibkr-gateway`.
     This makes Railway use the `railway.toml` + `Dockerfile` in this
     directory (separate from the bot's root `railway.toml`).
   - **Settings → Networking:** leave public networking OFF. The service
     is internal-only.

2. **Environment variables.** In the new service's **Variables** tab,
   add the secrets below. None of these belong in any file in the repo:

   | Variable | Value | Notes |
   |---|---|---|
   | `IBEAM_ACCOUNT` | your IB username | Live or paper, your choice |
   | `IBEAM_PASSWORD` | your IB password | |
   | `IBEAM_KEY` | random 32-char string | ibeam encrypts the above on disk |
   | `IBEAM_TWO_FA_HANDLER` | `NONE` / `GMAIL` / blank | See 2FA below |
   | `IBEAM_TOTP_SECRET` | (TOTP base32) | Only if using TOTP |

   **2FA decision tree:**
   - **Paper account:** `IBEAM_TWO_FA_HANDLER=NONE`. Easiest. No 2FA required.
   - **Live account with TOTP:** set `IBEAM_TOTP_SECRET` to the base32
     secret you got when you set up TOTP in IB account management.
     Don't set `IBEAM_TWO_FA_HANDLER` (default code-gen path will use the
     secret).
   - **Live account with IBKR mobile app push:** more complex — operator
     decision. Either switch the IB account to TOTP-only, or use ibeam's
     `GMAIL` handler (requires Gmail OAuth credentials, see
     [voyz/ibeam](https://github.com/Voyz/ibeam#2-factor-authentication)).

3. **Deploy.** Push to `main` (already done — the Dockerfile + railway.toml
   in this directory are in the repo). Railway picks up the new service
   on its next build. First boot takes ~3 min — ibeam pulls IB Gateway
   from IBKR, logs in, and starts serving the API.

4. **Verify the gateway is up.** From a shell with access to the Railway
   project, exec into the bot service and run:
   ```
   python -m tools.price_feed_ibkr --healthcheck SPY
   ```
   Expect `ibeam gateway authenticated: True`. If `False`, the IBKR
   credentials in step 2 are wrong; if `None`, the gateway service is
   unreachable.

5. **Flip the bot to IBKR.** On the BOT service (not the gateway service),
   add these two variables:
   ```
   PRICE_FEED=ibkr
   IBKR_GATEWAY_URL=http://ibkr-gateway.railway.internal:5000
   ```
   Bot restarts automatically. From this point, `yfinance_client.fetch_raw`
   routes through IBKR first and falls back to yfinance on any error.

## Daily operations

ibeam handles re-auth headlessly every ~24h. The bot doesn't need to know.
The `/v1/api/iserver/auth/status` endpoint reports current auth state and
is the healthcheck Railway pings.

If the gateway flaps:
- Bot stays up. Every fetch falls back to yfinance (logged at INFO as
  `price_feed degraded: ibkr returned no quote for X; falling back to
  yfinance`). No alerts get dropped.
- Operator can `railway logs --service ibkr-gateway` to see ibeam's view.
- To force a re-login, restart the gateway service in Railway dashboard.

## Conid cache

`migrations/029_ibkr_conid_cache.sql` adds a per-ticker conid cache so the
secdef/search call only fires once per ticker per lifetime. The bot reads
the cache before every quote; on a miss, it resolves and writes back.
