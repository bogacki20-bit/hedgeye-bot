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
ASSETS = {"Gold": "XAU", "Silver": "XAG", "EUR": "EURUSD", "GBP": "GBPUSD", "CAD": "CADUSD",
          "BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}


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
        out.append(dict(label=label, side=side.get(n), px=p, day=day, lo=lo, hi=hi, src=src, asof=asof, rp=rp,
                        trade=k["trade"][0], trend_lo=k["trend_long"][0], trend_hi=k["trend_short"][0]))
    return out


def _lines(o: dict) -> str:
    p, bits = o["px"], []
    if p and o["trade"]:
        bits.append(f"{'above' if p > o['trade'] else 'below'} Trade")
    if p and o["trend_lo"] and o["trend_hi"]:
        bits.append("above Trend" if p > o["trend_hi"] else "below Trend" if p < o["trend_lo"] else "in the Trend cloud")
    return ", ".join(bits) or "no lines"


def _num(x) -> str:
    return "-" if x is None else f"{x:,.4f}" if x < 10 else f"{x:,.2f}" if x < 1000 else f"{x:,.0f}"


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
    order = [f"{o['label']} ({gap(o) * 100:+.0f}% vs Trend)" for o in strong] + ["USD"] +             [f"{o['label']} ({gap(o) * 100:+.0f}% vs Trend)" for o in weak]
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
    g, s = (next((o for o in rows if o["label"] == x), None) for x in ("Gold", "Silver"))
    if g and s and g["px"] and s["px"]:
        lean = ("no lean: same trend on both" if g["side"] == s["side"]
                else f"hold {'Gold' if g['side'] == 'long' else 'Silver'} against the other")
        out += ["", f"Gold/silver ratio: {g['px'] / s['px']:.1f} ({lean}; gold rp {g['rp']:.2f}, silver rp {s['rp']:.2f})"
                if g["rp"] is not None and s["rp"] is not None else f"Gold/silver ratio: {g['px'] / s['px']:.1f} ({lean})"]
    out += ["", "Metals are priced off the front future and Hedgeye's range is spot, so their rp can be a little off.",
            "No sizes: the bot has no Kinesis holdings."]
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(report(read(), dt.datetime.now()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
