"""tools/sys_flows_cot.py - the regulator's weekly count of what funds hold in futures.

Source: CFTC Commitments of Traders, public data service (no key). Out Friday ~3:30 PM ET for
the Tuesday before, so it is 3 days old when it lands and up to 10 days old before the next.
  financial futures (stocks, Treasuries, currencies, bitcoin): the "leveraged funds" bucket
  commodities: the "managed money" bucket
net = long - short, in contracts. We keep 3 years so each net can be ranked against its own
history (pctile: 0 = most short in 3 years, 100 = most long).

Read it right: leveraged funds in Treasuries also hold the cash-vs-futures basis trade, a large
standing short that is not a trend bet. For bonds the weekly CHANGE is the signal, not the level.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from tools.sys_flows_markets import MARKETS

API = "https://publicreporting.cftc.gov/resource/{ds}.json"
DATASETS = {
    "tff": ("gpe5-46if", "lev_money_positions_long", "lev_money_positions_short", "leveraged funds"),
    "dis": ("72hh-3qpy", "m_money_positions_long_all", "m_money_positions_short_all", "managed money"),
}


def fetch(weeks=160):
    """[(report_date, code, market key, group, long, short, net, oi)] oldest first."""
    since = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%dT00:00:00.000")
    out = []
    for kind, (ds, col_l, col_s, group) in DATASETS.items():
        codes = {m["cot"][1]: m["key"] for m in MARKETS if m.get("cot") and m["cot"][0] == kind}
        if not codes:
            continue
        q = {"$select": f"report_date_as_yyyy_mm_dd,cftc_contract_market_code,{col_l},{col_s},open_interest_all",
             "$where": "cftc_contract_market_code in(" + ",".join(f"'{c}'" for c in codes) + ")"
                       f" AND report_date_as_yyyy_mm_dd >= '{since}'",
             "$limit": "50000"}
        req = urllib.request.Request(API.format(ds=ds) + "?" + urllib.parse.urlencode(q),
                                     headers={"User-Agent": "Mozilla/5.0"})
        for r in json.loads(urllib.request.urlopen(req, timeout=90).read()):
            try:
                lg, sh = int(float(r[col_l])), int(float(r[col_s]))
            except (KeyError, ValueError):
                continue
            out.append((datetime.strptime(r["report_date_as_yyyy_mm_dd"][:10], "%Y-%m-%d").date(),
                        r["cftc_contract_market_code"], codes[r["cftc_contract_market_code"]], group,
                        lg, sh, lg - sh, int(float(r.get("open_interest_all") or 0))))
    return sorted(out)


def save(conn, rows):
    from psycopg2.extras import execute_values
    cur = conn.cursor()
    execute_values(cur, """INSERT INTO sys_flow_cot (report_date, code, market, trader_group, pos_long, pos_short, net, oi)
                       VALUES %s
                       ON CONFLICT (report_date, code) DO UPDATE SET market=EXCLUDED.market,
                         trader_group=EXCLUDED.trader_group, pos_long=EXCLUDED.pos_long,
                         pos_short=EXCLUDED.pos_short, net=EXCLUDED.net, oi=EXCLUDED.oi""", rows, page_size=1000)
    conn.commit()


def summary(rows):
    """{market: {date, net, chg1w, chg4w, pct_oi, pctile, group}} from the latest report."""
    hist = {}
    for d, _, key, group, _, _, net, oi in rows:
        hist.setdefault(key, []).append((d, net, oi, group))
    out = {}
    for key, h in hist.items():
        h.sort()
        d, net, oi, group = h[-1]
        nets = [x[1] for x in h]
        out[key] = {"date": d, "net": net, "group": group,
                    "chg1w": net - h[-2][1] if len(h) >= 2 else None,
                    "chg4w": net - h[-5][1] if len(h) >= 5 else None,
                    "pct_oi": 100.0 * net / oi if oi else None,
                    "chg1w_pct_oi": 100.0 * (net - h[-2][1]) / oi if oi and len(h) >= 2 else None,
                    "pctile": round(100.0 * sum(n <= net for n in nets) / len(nets)) if len(nets) >= 52 else None}
    return out
