"""Kinesis exchange, read-only (Kris 11/22/2026).

Two sources:
  snapshots()   Kinesis's public market-data feed: bid, ask, mid and 24-hour volume for every pair. No key.
  holdings()    Kris's Kinesis balances. Needs an API key he creates in his Kinesis account settings, saved in
                the bot's local .env as KINESIS_PUBLIC_KEY and KINESIS_PRIVATE_KEY. Never printed, never committed.

This module only ever sends GET requests. It cannot place, change or cancel an order, and it must stay that
way: the bot recommends, Kris trades.

    py -m tools.kinesis_api                 # live spreads on the main pairs
    py -m tools.kinesis_api --holdings      # balances (once the key is in .env)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_URL = "https://fastapi.kinesis.money/api/market-data/snapshots/all/public"
ACCOUNT_URL = "https://client-api.kinesis.money"
HOLDINGS_PATH = "/v1/exchange/holdings"
FEE = 0.0022            # Kinesis's fee page, 11/22/2026: 0.22% per trade on the exchange, limit orders included
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
MAIN = ("KAU_USD", "KAG_USD", "KAU_KAG", "BTC_USD", "ETH_USD", "SOL_USD", "XRP_USD", "AVAX_USD",
        "BTC_KAU", "ETH_KAU", "SOL_KAU", "SOL_KAG", "XRP_KAU", "XRP_KAG",
        "KAU_C1EUR", "KAG_C1EUR", "KAU_C1GBP", "KAG_C1GBP", "KAU_C1CAD", "KAG_C1CAD", "KAU_C1JPY", "KAG_C1JPY")


def _get(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})}, method="GET")     # GET only, always
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def snapshots() -> dict:
    """pair id -> dict(bid, ask, mid, spread_pct, vol_usd). Pairs with no two-sided quote are left out."""
    out = {}
    for s in _get(MARKET_URL):
        bid, ask = s.get("bidPrice"), s.get("askPrice")
        if not bid or not ask or ask <= 0 or bid <= 0:
            continue
        mid = (bid + ask) / 2
        out[s["symbolId"]] = dict(bid=bid, ask=ask, mid=mid, spread_pct=max(0.0, (ask - bid) / mid * 100),    # a crossed quote counts as zero
                                  vol_usd=s.get("dailyVolumeUsd") or 0)
    return out


def _keys():
    """(public, private) from the environment or the bot's .env; (None, None) when there is no key yet."""
    pub, prv = os.environ.get("KINESIS_PUBLIC_KEY"), os.environ.get("KINESIS_PRIVATE_KEY")
    if pub and prv:
        return pub, prv
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                v = v.strip().strip('"').strip("'")
                if k.strip() == "KINESIS_PUBLIC_KEY":
                    pub = v
                elif k.strip() == "KINESIS_PRIVATE_KEY":
                    prv = v
    except OSError:
        pass
    return (pub or None), (prv or None)


def holdings():
    """Kris's Kinesis balances as the API returns them, or None when no key is saved yet."""
    pub, prv = _keys()
    if not pub or not prv:
        return None
    nonce = str(int(time.time() * 1000))
    sig = hmac.new(prv.encode(), f"{nonce}GET{HOLDINGS_PATH}".encode(), hashlib.sha256).hexdigest().upper()
    return _get(ACCOUNT_URL + HOLDINGS_PATH, {"X-API-Key": pub, "X-Nonce": nonce, "X-Signature": sig})


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdings", action="store_true", help="print balances (needs the key in .env)")
    args = ap.parse_args()
    if args.holdings:
        try:
            h = holdings()
        except Exception as e:                                       # noqa: BLE001
            print(f"Kinesis holdings failed: {type(e).__name__} {getattr(e, 'code', '')}".strip())
            return 1
        if h is None:
            print("No Kinesis key saved yet (KINESIS_PUBLIC_KEY and KINESIS_PRIVATE_KEY in .env).")
            return 0
        print(json.dumps(h, indent=1)[:4000])
        return 0
    snap = snapshots()
    print(f"Kinesis live quotes, {len(snap)} two-sided pairs. Fee {FEE * 100:.2f}% per trade on top of the spread.")
    for p in MAIN:
        s = snap.get(p)
        if s:
            print(f"  {p:10} bid {s['bid']:<14.8g} ask {s['ask']:<14.8g} spread {s['spread_pct']:5.2f}%  "
                  f"24h ${s['vol_usd']:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
