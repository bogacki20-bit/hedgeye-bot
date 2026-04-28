# Hedgeye Bot — Setup Guide

## What this bot does
- Scrapes app.hedgeye.com every 15 minutes while you sleep
- Watches your iCloud inbox for Hedgeye emails
- Uses Claude AI to classify every piece of content and extract trade signals
- Texts you immediately when Keith posts a high-conviction signal
- Sends you a morning brief at 7am with everything from overnight

---

## Step 1 — Generate your iCloud App-Specific Password

1. Go to https://appleid.apple.com on your Mac
2. Sign in with your Apple ID
3. Click **Sign-In & Security**
4. Click **App-Specific Passwords**
5. Click the **+** button
6. Name it "Hedgeye Bot" and click Create
7. Copy the password shown (looks like: xxxx-xxxx-xxxx-xxxx)
8. Paste it into your `.env` file next to `ICLOUD_APP_PASSWORD=`

---

## Step 2 — Set up Pushover (for phone notifications)

Pushover is a push-notification service. The bot uses it to alert you on
your phone whenever a Hedgeye email arrives, with a sized trade
recommendation for high-conviction signals.

1. **Create a Pushover account.** Go to https://pushover.net and click
   **Login or Signup** → **Signup**. Free; verify your email address.

2. **Install the Pushover app on your phone.** It's a one-time $5 license
   per platform (iOS or Android), unlocked after the 30-day free trial.
   - iOS:     https://apps.apple.com/app/pushover-notifications/id506088175
   - Android: https://play.google.com/store/apps/details?id=net.superblock.pushover

   Sign in with the account you just created. Your phone is now registered
   to receive notifications.

3. **Copy your User Key.** Once logged into pushover.net, your **User Key**
   is shown in a box at the top-right of the dashboard — a 30-character
   string. Copy it. This is `PUSHOVER_USER`.

4. **Create an Application/API Token.**
   - On the dashboard, scroll to **Your Applications** → click
     **Create an Application/API Token**.
   - Name it `Hedgeye Bot`. Type can stay as **Application**.
   - Accept the terms and click **Create Application**.
   - The next page shows your **API Token/Key** — another 30-character
     string. Copy it. This is `PUSHOVER_TOKEN`.

5. **Add both values to Railway.**
   - Open https://railway.app and select your project → service.
   - Click the **Variables** tab.
   - Click **+ New Variable** and add:
     ```
     PUSHOVER_USER=<your user key from Step 3>
     PUSHOVER_TOKEN=<your api token from Step 4>
     ```
   - Click **Add** for each. Railway will redeploy automatically.

   On startup the bot sends a test ping titled **"Hedgeye Bot"** with the
   message **"Bot started on Railway. Pushover OK."** — if it lands on
   your phone, both values are correct.

   (If you also keep a local `.env` for development, add the same two
   lines there.)

---

## Step 3 — Get your Anthropic API key

1. Go to https://console.anthropic.com
2. Sign in (or create a free account)
3. Click **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-`)
5. Paste into `.env` next to `ANTHROPIC_API_KEY=`

---

## Step 4 — Fill in the rest of your .env file

Open `.env.example`, rename it to `.env`, and fill in:
- `HEDGEYE_EMAIL` — your Hedgeye login email
- `HEDGEYE_PASSWORD` — your Hedgeye password
- `ICLOUD_EMAIL` — Bogacki20@icloud.com

---

## Step 5 — Deploy to Railway

1. Go to https://railway.app and click **Start a New Project**
2. Choose **Deploy from local directory**
3. Install the Railway CLI when prompted:
   ```
   brew install railway
   ```
4. In your terminal, navigate to this folder:
   ```
   cd hedgeye-bot
   railway login
   railway up
   ```
5. In the Railway dashboard, click your project → **Variables**
6. Click **Raw Editor** and paste the entire contents of your `.env` file
7. Railway will restart the bot automatically with your credentials

---

## Step 6 — Add a persistent volume (so your data survives restarts)

1. In Railway, click your service → **Add Volume**
2. Mount path: `/data`
3. That's it — your database will persist across restarts

---

## What you'll receive on your phone

**Immediate alert** (any time of day) when Keith posts Best Idea or Adding signals:
```
🚨 Hedgeye Signal
Long XLE — Best Idea
Energy sector long thesis: supply constraints with...
```

**Morning brief at 7am ET:**
```
📊 Hedgeye Morning Brief — Apr 24
🟢 SIGNALS (3):
  Long XLE — Best Idea
  Long JPM — Adding
  Long GLD — Best Idea
📈 MACRO (2):
  SPX long gamma, vol control buying, support 7000
🔬 RESEARCH: 4 new notes
app.hedgeye.com
```

---

## Troubleshooting

**Bot stopped texting me:**
- Check Railway dashboard for errors in the logs
- Most common cause: Hedgeye changed their login page HTML

**Getting too many texts:**
- Adjust conviction filter in `scraper.py` line with `"Best Idea", "Adding"`
- Remove "Adding" to only get Best Idea alerts

**iCloud connection failing:**
- Your app-specific password may have expired — generate a new one

---

## Cost estimate (monthly)
- Railway: ~$5/mo (Hobby plan)
- Pushover: free for the first 10,000 notifications/month, then a one-time $5 license fee per platform (iOS/Android)
- Anthropic API: ~$3-8/mo depending on volume (35-50 emails/day × ~$0.003 each)
- **Total: ~$10-15/month**
