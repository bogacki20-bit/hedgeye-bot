# Operator tasks — work order 2026-06-10 night, item 8

Code-side hygiene is committed (rogue table dropped, "~$134" sizing
stripped from price_monitor.compose_recommendation). These two items
need YOU at the keyboard — no code I can write here.

## 1. Paste SS PNG list into config/ss_full_list.yaml

Open the latest Signal Strength PNG attachment and add the full ticker
list. The Monday SS email is the canonical source. ~2 min.

## 2. Append outputs/mfr_to_add.txt to the MFR watchlist

There's no programmatic-add endpoint in mfr_client.py — the MFR API as
wired is GET-only. To add tickers you have to use the MFR web UI:

  1. Open https://myfractalrange.com/.
  2. Watchlist → Add tickers.
  3. Paste from outputs/mfr_to_add.txt (the "ADDABLE TO MFR WATCHLIST"
     block — skip the FX / macro composite block at the bottom).
  4. After save, run `py mfr_client.py --fanout` to refresh snapshots
     for the newly-added tickers so they're available to the next scan.

156 tickers in the addable block (as of 2026-05-26 gap report).

## 3. Tomorrow's cron — Quad rotation

Heads-up: cron tomorrow updates the Quad. detect_quads.run() now goes
through tools.quad_regime.set_quads() so the dual short/legacy bot_state
keys and the quad_regime_history row land in one transaction, and any
real rotation pushes ONE Telegram notice. Nothing for you to do; just
expect to see the rotation alert if Q3 → something else.
