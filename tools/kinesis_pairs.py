"""Kinesis pair view (Kris 11/22/2026): which asset to hold against which.

In Kinesis, gold and silver trade against each other and against USD, EUR, GBP, CAD, BTC, ETH and SOL. Nothing
is shorted there: a bearish view is expressed by selling the weak asset for a strong one. So this ranks the
nine assets by Hedgeye's read of each one against the dollar and prints the swaps the rule card allows.

  Read per asset   Keith's trend call and daily range (Risk Range rows; Crypto Quant for ETH and SOL), rp,
                   and price against the bot's Trade line and Trend cloud. All through tools/rule_scanner.py,
                   so the range order and the freshness rule are the same as everywhere else.
  Rank             Bullish assets first, then USD (the yardstick), then Bearish assets; inside a group, by how
                   far price sits from its Trend cloud.
  Swaps (card)     BUY a Bullish asset with the weakest one when it is low in its range (rp <= 0.35) on a red day.
                   SELL a Bearish asset for the strongest one on a bounce (rp >= 0.65) on a green day.
                   Never sell the bottom of the range (rp <= 0.15) and never buy the top (rp >= 0.85).

Recommend-only. The bot holds no Kinesis positions, so nothing here is sized.

    py -m tools.kinesis_pairs
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

# label shown -> scanner name. USD is the yardstick and has no row of its own here.
# Read off the exchange's own Markets list on 11/22/2026 (132+ pairs): gold (KAU, 1 gram) and silver (KAG,
# 1 oz) each trade against USD, EUR, GBP, CAD, JPY and other currencies, and against BTC, ETH, SOL, XRP and
# a few smaller coins; gold trades against silver; the coins also trade against USD. Only assets Hedgeye
# gives a daily range for are ranked here.
ASSETS = {"Gold": "XAU", "Silver": "XAG", "EUR": "EURUSD", "GBP": "GBPUSD", "CAD": "CADUSD", "JPY": "USDJPY",
          "BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "XRP": "XRP", "AVAX": "AVAX"}
FLIP = {"JPY"}          # Hedgeye quotes USD/YEN; the yen itself is that row turned upside down
USD_ONLY = {"AVAX"}     # trades against the dollar only on Kinesis: no metal pair
# What the order books looked like on Sunday 11/22/2026 (one look; re-check before leaning on it).
SPREAD_NOTE = ("Order books seen 11/22: BTC/USD about 0.01% wide; gold/USD about 1.1%; gold/silver about 5.5%. "
               "24-hour volume was thin: silver/USD $39k, gold/USD $34k, XRP/USD $28k, BTC/USD $6k, SOL/USD $3k, "
               "gold/silver $1.7k, ETH/USD almost none. A swap has to clear the spread to gain anything.")


REL_GAP = 0.40      # how far apart two legs' rp must be before a holdings swap is listed


def read() -> list[dict]:
    import yfinance as yf
    from tools import rule_scanner as R
    from tools.level_lines import lookup

    names = list(ASSETS.values())
    side = R.extra_inventory()
    rng = R.load_ranges(names)
    lv = lookup([R._line_key(n) for n in names])
    out = []
    for label, n in ASSETS.items():
        try:
            cl = yf.Ticker(R.YF_KEY[n]).history(period="1mo", interval="1d").Close.dropna().values
            p, day = float(cl[-1]), (cl[-1] / cl[-2] - 1) * 100
        except Exception:                                           # noqa: BLE001
            p = day = None
        hi, lo, src, asof = rng.get(n, (None,) * 4)
        rp = (p - lo) / (hi - lo) if p and hi and lo else None
        k = lv[R._line_key(n)]
        o = dict(label=label, side=side.get(n), px=p, day=day, lo=lo, hi=hi, src=src, asof=asof, rp=rp,
                 trade=k["trade"][0], trend_lo=k["trend_long"][0], trend_hi=k["trend_short"][0])
        if label in FLIP:       # turn the USD/YEN row into the yen: every price is 1/x, high and low trade places
            inv = lambda x: 1 / x if x else None                    # noqa: E731
            o.update(side={"long": "short", "short": "long"}.get(o["side"]), px=inv(p), lo=inv(hi), hi=inv(lo),
                     day=-day if day is not None else None, rp=1 - rp if rp is not None else None,
                     trade=inv(o["trade"]), trend_lo=inv(o["trend_hi"]), trend_hi=inv(o["trend_lo"]))
        out.append(o)
    return out


def _lines(o: dict) -> str:
    p, bits = o["px"], []
    if p and o["trade"]:
        bits.append(f"{'above' if p > o['trade'] else 'below'} Trade")
    if p and o["trend_lo"] and o["trend_hi"]:
        bits.append("above Trend" if p > o["trend_hi"] else "below Trend" if p < o["trend_lo"] else "in the Trend cloud")
    return ", ".join(bits) or "no lines"


def _num(x) -> str:
    return "-" if x is None else f"{x:.5f}" if x < 0.1 else f"{x:,.4f}" if x < 10 else f"{x:,.2f}" if x < 1000 else f"{x:,.0f}"


def report(rows: list[dict], now: dt.datetime) -> str:
    bull = sorted((o for o in rows if o["side"] == "long"), key=lambda o: 9 if o["rp"] is None else o["rp"])
    bear = sorted((o for o in rows if o["side"] == "short"), key=lambda o: -9 if o["rp"] is None else -o["rp"])
    none = [o for o in rows if o["side"] not in ("long", "short")]
    out = [f"Kinesis pair view, as of {now:%m/%d %H:%M}", "", "Each asset against the dollar (Hedgeye's trend and daily range):"]
    for o in bull + bear + none:
        tr = {"long": "Bullish", "short": "Bearish"}.get(o["side"], "no call")
        rp = "-" if o["rp"] is None else f"{o['rp']:.2f}"
        rg = f"{_num(o['lo'])}-{_num(o['hi'])} ({o['src']} {o['asof']:%m/%d})" if o["lo"] else "no fresh range"
        day = "" if o["day"] is None else f" ({o['day']:+.1f}% last session)"
        out.append(f"  {o['label']:7} {tr:8} rp {rp:5} | {_num(o['px'])}{day} | range {rg} | {_lines(o)}")
    # strength inside a group = how far price sits from its Trend cloud (the bottom edge), most above first
    def gap(o):
        return (o["px"] / o["trend_lo"] - 1) if o["px"] and o["trend_lo"] else -9
    strong = sorted(bull, key=gap, reverse=True)
    weak = sorted(bear, key=gap, reverse=True)
    order = ([f"{o['label']} ({gap(o) * 100:+.0f}% vs Trend)" for o in strong] + ["USD"]
             + [f"{o['label']} ({gap(o) * 100:+.0f}% vs Trend)" for o in weak])
    out += ["", "Strongest to weakest: " + " > ".join(order)]
    best = strong[0]["label"] if strong else "USD"
    worst = weak[-1]["label"] if weak else "USD"
    acts = []
    for o in bull:
        if o["rp"] is not None and o["rp"] <= 0.35 and (o["day"] or 0) < 0:
            acts.append(f"  BUY {o['label']} with {worst} (one step): Bullish, rp {o['rp']:.2f}, red day")
        elif o["rp"] is not None and o["rp"] >= 0.85:
            acts.append(f"  TRIM {o['label']} into USD: Bullish but rp {o['rp']:.2f}, top of the range")
    for o in bear:
        if o["rp"] is not None and o["rp"] >= 0.65 and (o["day"] or 0) > 0:
            acts.append(f"  SELL {o['label']} for {best} (one step): Bearish, rp {o['rp']:.2f}, green day")
    out += ["", "Swaps the card allows now:"] + (acts or ["  none"])
    wait = [f"{o['label']} (rp {o['rp']:.2f})" for o in bear if o["rp"] is not None and o["rp"] <= 0.15]
    if wait:
        out.append("  Bottom of the range, don't sell here; wait for a bounce: " + ", ".join(wait))
    stalk = [f"{o['label']} (rp {o['rp']:.2f})" for o in bear if o["rp"] is not None and 0.15 < o["rp"] < 0.65]
    if stalk:
        out.append("  Bearish, sell on a bounce to rp 0.65+: " + ", ".join(stalk))
    # Swaps to gain holdings, not dollars (Kris 11/22). Every Kinesis pair has a metal on one side. Move out of
    # the leg that is high in its own Hedgeye range into the leg that is low in its range, and swap back when
    # that flips: more ounces or coins either way. Never into a leg with a weaker trend call.
    rank = {"long": 2, "short": 0}
    rel = []
    for a in rows:
        for b in rows:
            if a is b or "Gold" not in (a["label"], b["label"]) and "Silver" not in (a["label"], b["label"]):
                continue
            if a["label"] in USD_ONLY or b["label"] in USD_ONLY:
                continue
            if a["rp"] is None or b["rp"] is None or rank.get(b["side"], 1) < rank.get(a["side"], 1):
                continue
            if a["rp"] - b["rp"] >= REL_GAP:
                rel.append((a["rp"] - b["rp"], f"  {a['label']} -> {b['label']}: {a['label']} rp {a['rp']:.2f}, "
                                               f"{b['label']} rp {b['rp']:.2f} (gap {a['rp'] - b['rp']:.2f})"))
    out += ["", f"Swaps to gain holdings (out of the leg high in its range, into the leg low in its range; gap {REL_GAP}+):"]
    out += [x for _, x in sorted(rel, reverse=True)] or ["  none"]
    out.append("  This is our own read of two Hedgeye ranges, not a card rule.")
    g, s = (next((o for o in rows if o["label"] == x), None) for x in ("Gold", "Silver"))
    if g and s and g["px"] and s["px"]:
        lean = ("no lean: same trend on both" if g["side"] == s["side"]
                else f"hold {'Gold' if g['side'] == 'long' else 'Silver'} against the other")
        out += ["", f"Gold/silver ratio: {g['px'] / s['px']:.1f} ({lean}; gold rp {g['rp']:.2f}, silver rp {s['rp']:.2f})"
                if g["rp"] is not None and s["rp"] is not None else f"Gold/silver ratio: {g['px'] / s['px']:.1f} ({lean})"]
    out += ["", "Metals are priced off the front future and Hedgeye's range is spot, so their rp can be a little off.",
            SPREAD_NOTE, "No sizes: the bot has no Kinesis holdings."]
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(report(read(), dt.datetime.now()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
