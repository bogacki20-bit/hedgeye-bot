"""desk_snapshot.py — one page other models can read to check the desk ("other eyes").

Kris 11/10/2026: he wants other models (his Claude.ai project on the phone,
OpenClaw) to check in on the bot. This writes a single self-contained snapshot
of the desk at the moment it runs:

  rules in force (Trade/Trend lines, the mandate), market (Keith's risk ranges,
  SpotGamma index levels, factor board), the book by account with each
  position's lines and rp, the rule scanner's actions, the watch list, ETF Pro
  shorts high in range, and a reviewer brief (what to audit).

Privacy: no account numbers; no Hedgeye text is quoted (levels and list
membership only, which are the bot's own data). The files stay on this laptop
(outputs/desk_snapshot/); publishing anywhere is a separate, explicit step.

    py -m tools.desk_snapshot            # writes desk_snapshot.md + .json
    py -m tools.desk_snapshot --no-scan  # skip the (slow) rule scanner
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import warnings

warnings.filterwarnings("ignore")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs" / "desk_snapshot"
CARD = ROOT / "docs" / "playbook" / "RULE_CARD.md"
WATCH = ROOT / "data" / "stalk" / "desk_watch.csv"
CASH = {"SPAXX**", "FDRXX", "CORE**", "BUXX", "Pending activity"}
KEY_RR = ["SPX", "COMPQ", "RUT", "UST2Y", "UST10Y", "UST30Y", "USD", "EUR/USD", "USD/YEN",
          "WTIC", "BRENT", "GOLD", "SILVER", "COPPER", "VIX", "NVDA", "BITCOIN"]

REVIEWER_BRIEF = """\
## Reviewer brief (for any model checking this desk)
You are a second set of eyes on a recommend-only risk desk that follows Hedgeye's
process. Kris makes every trade himself. Audit, don't flatter. Check:
1. **Misses:** names in Hedgeye inventory (Signal Strength, Position Monitor, ETF Pro)
   that meet the card's entry rules but are NOT in "Scanner actions" or the watch list.
2. **Rule violations:** any call that contradicts the lines (Trend = MFR cloud: bottom
   for longs, top for shorts; Keith's published number wins), sizing MIN/MAX, the
   green-day rule for shorts, or the red-day / range-low rule for longs.
3. **Conflicts:** Hedgeye on both sides of a name, or the desk's view overriding the card.
4. **Rotation:** does the book match the mandate (patient Quad 4 long/short build, some
   Quad 2/3 earners, fast capital rotation)? Where is capital stuck?
5. **Risk:** positions closest to their exit line, concentration, cash drag.
Answer in short bullets: MISSES, VIOLATIONS, CONFLICTS, ROTATION, RISK. Cite tickers and numbers
from this page only; say "not on the page" rather than guess.
"""


def _lines_section() -> str:
    try:
        txt = CARD.read_text(encoding="utf-8")
        i, j = txt.find("## Lines"), txt.find("## Position size")
        return txt[i:j].strip() if i >= 0 and j > i else ""
    except OSError:
        return ""


def _book(cur):
    cur.execute("""select b.account_name, b.symbol, b.description, b.quantity, b.market_value,
                          b.cost_basis, b.snapshot_date
                   from book_positions b
                   where b.snapshot_date=(select max(snapshot_date) from book_positions)
                     and b.quantity <> 0 order by b.account_name, b.market_value desc""")
    return cur.fetchall()


def build(scan: bool = True) -> dict:
    import db_pg
    import yfinance as yf
    from tools.level_lines import lookup

    now = dt.datetime.now()
    snap = {"as_of": now.strftime("%Y-%m-%d %H:%M ET")}
    with db_pg.get_conn() as c, c.cursor() as cur:
        rows = _book(cur)
        cur.execute("select account_name, sum(market_value) from book_positions "
                    "where snapshot_date=(select max(snapshot_date) from book_positions) group by 1")
        acct_tot = {a: float(v) for a, v in cur.fetchall()}
        cur.execute("""select distinct on (ticker) ticker, trend, buy_trade, sell_trade, prev_close
                       from hedgeye_risk_ranges where ticker = any(%s)
                       order by ticker, signal_date desc""", (KEY_RR,))
        rr = cur.fetchall()
        cur.execute("""select sym, ref_price, vol_trigger, zero_gamma, call_wall, put_wall, risk_pivot, note_date
                       from sg_index_levels where note_date=(select max(note_date) from sg_index_levels)""")
        sg = cur.fetchall()
        cur.execute("""select distinct on (ticker) ticker, range_low, range_high, bias, week_of
                       from hedgeye_etf_pro_ranges where week_of >= current_date - 10
                       order by ticker, week_of desc""")
        etf = cur.fetchall()
        cur.execute("select ticker, side from ss_roster_history where removed_on is null")
        ss = cur.fetchall()
        cur.execute("select ticker, hedgeye_bucket_0629 from ticker_tags where hedgeye_bucket_0629 is not null")
        pm = cur.fetchall()
        # 11/12 (Kris): reviewers and the desk must work off the same, current Hedgeye data —
        # stamp every feed's date so a stale section is obvious.
        cur.execute("""select (select max(snapshot_date) from book_positions),
                              (select max(signal_date) from hedgeye_risk_ranges),
                              (select max(week_of) from hedgeye_etf_pro_ranges),
                              (select max(snapshot_date) from hedgeye_signal_strength),
                              (select max(signal_ts) from hedgeye_rta),
                              (select max(note_date) from sg_index_levels),
                              (select max(bar_date) from tv_levels),
                              (select max(report_date) from t1a_snapshots),
                              (select max(as_of)::date from hedgeye_quad)""")
        f = cur.fetchone()
        snap["data_as_of"] = dict(zip(["Fidelity book", "Hedgeye risk ranges", "ETF Pro weekly ranges",
                                       "Signal Strength lists", "Latest Real-Time Alert", "SpotGamma levels",
                                       "TradingView levels (bar)", "Tier 1 Alpha report", "Quad (dashboard)"],
                                      [str(x)[:16] if x else "none" for x in f]))

    held = sorted({r[1] for r in rows if r[1] not in CASH and r[1].isalpha()})
    etf_short = [e[0] for e in etf if e[3] == "short"]
    px_syms = sorted(set(held) | set(etf_short))
    try:
        px = yf.download(px_syms, period="5d", progress=False)["Close"].ffill().iloc[-1]
    except Exception:
        px = {}
    lines = lookup(held) if held else {}
    import db_pg as _db
    with _db.get_conn() as c2, c2.cursor() as cur2:   # raw line values (not the desk's chosen line)
        # 11/15 (Kris "Yes, switch it"): MFR first, TradingView backup (v_line_source, migration 140)
        cur2.execute("""select case when key = 'USD_SEMI' then 'USD' else key end, cloud_lower, cloud_upper, trade
                        from v_line_source where key = any(%s)""",
                     ([("USD_SEMI" if t == "USD" else t) for t in held],))
        tvraw = {t: (lo, hi, vb) for t, lo, hi, vb in cur2.fetchall()}
        # 11/12 (Kris: "do we have the levels in there"): the bot's working range per name
        cur2.execute("select ticker, range_low, range_high, band_source from v_screener where ticker = any(%s)", (held,))
        brange = {t: (lo, hi, src) for t, lo, hi, src in cur2.fetchall() if lo is not None and hi is not None}

    book = []
    for a, s, desc, q, mv, cb, sd in rows:
        q, mv = float(q or 0), float(mv or 0)
        item = {"account": a, "ticker": s, "what": (desc or "").title()[:48], "qty": round(q, 3),
                "value": round(mv), "pct_of_acct": round(100 * mv / acct_tot[a], 1) if acct_tot.get(a) else None}
        if s in tvraw:
            lo, hi, vb = tvraw[s]
            item.update({"cloud_lo": float(lo) if lo else "", "cloud_hi": float(hi) if hi else "",
                         "vola": float(vb) if vb else ""})
        if s in brange:
            lo, hi, src = brange[s]
            item["range"] = f"{float(lo):g}-{float(hi):g} ({ {'mfr': 'MFR', 'tv': 'TradingView', 'hdg': 'Hedgeye', 'iin': 'Investing Ideas'}.get(src, src) })"
        if s not in CASH and s in lines:
            L, p = lines[s], (float(px[s]) if s in px else None)
            ln = L["trend_long"] if q > 0 else L["trend_short"]
            item.update({"price": round(p, 2) if p else None,
                         "trend_line": ln[0], "trend_src": ln[1],
                         "trade_line": L["trade"][0],
                         "room_pct": round(100 * (p / ln[0] - 1) * (1 if q > 0 else -1), 1) if p and ln[0] else None})
            ep = L.get("etf_pro")
            if ep and ep[0] is not None and ep[1] is not None:   # 11/11: one-sided ranges crashed --raw
                item["etf_range"] = f"{ep[0]:g}-{ep[1]:g}"
                if p:
                    item["rp"] = round((p - ep[0]) / (ep[1] - ep[0]), 2)
        book.append(item)
    snap["book_date"] = str(rows[0][6]) if rows else None
    snap["accounts"] = {a: round(v) for a, v in acct_tot.items()}
    snap["book"] = book

    snap["risk_ranges"] = [{"ticker": t, "trend": tr, "low": float(lo), "high": float(hi), "prev": float(pc)}
                           for t, tr, lo, hi, pc in rr]
    snap["spotgamma"] = [{"sym": s, "ref": float(r or 0), "vol_trigger": float(vt or 0), "zero_gamma": float(z or 0),
                          "call_wall": float(cw or 0), "put_wall": float(pw or 0),
                          "risk_pivot": float(rp) if rp else None, "date": str(d)}
                         for s, r, vt, z, cw, pw, rp, d in sg]
    shorts = []
    for t, lo, hi, b, wk in etf:
        if b == "short" and t in px and lo and hi:
            rp = (float(px[t]) - float(lo)) / (float(hi) - float(lo))
            if rp >= 0.65:
                shorts.append({"ticker": t, "price": round(float(px[t]), 2), "keith_low": float(lo),
                               "keith_high": float(hi), "rp": round(rp, 2)})
    snap["etf_pro_shorts_high_in_range"] = sorted(shorts, key=lambda x: -x["rp"])
    snap["inventory_counts"] = {"signal_strength": len(ss), "position_monitor": len(pm), "etf_pro": len(etf)}

    try:
        with open(WATCH, newline="", encoding="utf-8") as f:
            snap["watch_list"] = list(csv.DictReader(f))
    except OSError:
        snap["watch_list"] = []

    try:
        from tools.factor_board import build as fb_build, hud as fb_hud
        snap["factor_board"] = fb_hud(fb_build())
    except Exception as e:  # never fail the snapshot on one section
        snap["factor_board"] = f"(factor board unavailable: {e})"

    if scan:
        try:
            from tools.rule_scanner import scan as rs_scan, fmt as rs_fmt
            snap["scanner"] = rs_fmt(rs_scan(now), now)
        except Exception as e:
            snap["scanner"] = f"(scanner unavailable: {e})"
    return snap


def to_md(s: dict) -> str:
    L = [f"# Kris's risk desk — snapshot as of {s['as_of']}",
         "Recommend-only desk on Hedgeye's process. Mandate (Kris 11/10): patiently build a large "
         "Quad 4 long/short book, carry some Quad 2/3 earners, trade around it, rotate capital fast.",
         "", _lines_section(), "",
         "## Accounts (book as of " + str(s.get("book_date")) + ")"]
    L += [f"- {a}: ${v:,}" for a, v in s["accounts"].items()]
    L += ["", "## Scanner actions (rule card applied mechanically)", "```", str(s.get("scanner", "(not run)")), "```",
          "", "## Factor board (rotate from → to)", "```", str(s.get("factor_board", "")), "```",
          "", "## Keith risk ranges (latest)", "| ticker | trend | low | high | prev close |", "|---|---|---|---|---|"]
    L += [f"| {r['ticker']} | {r['trend']} | {r['low']:g} | {r['high']:g} | {r['prev']:g} |" for r in s["risk_ranges"]]
    if s["spotgamma"]:
        L += ["", f"## SpotGamma index levels ({s['spotgamma'][0]['date']})",
              "| sym | ref | vol trigger | zero gamma | call wall | put wall | risk pivot |", "|---|---|---|---|---|---|---|"]
        L += [f"| {g['sym']} | {g['ref']:g} | {g['vol_trigger']:g} | {g['zero_gamma']:g} | {g['call_wall']:g} | "
              f"{g['put_wall']:g} | {g['risk_pivot'] or ''} |" for g in s["spotgamma"]]
    L += ["", "## Book by account (Trend line = cloud bottom for longs / top for shorts unless Keith's number)",
          "| account | ticker | what | qty | $ | % acct | price | rp | trend line (src) | room % |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for b in s["book"]:
        L.append(f"| {b['account']} | {b['ticker']} | {b['what']} | {b['qty']:g} | {b['value']:,} | "
                 f"{b.get('pct_of_acct', '')} | {b.get('price', '')} | {b.get('rp', '')} | "
                 f"{b.get('trend_line', '')} ({b.get('trend_src', '')}) | {b.get('room_pct', '')} |")
    L += ["", "## ETF Pro shorts at rp ≥ 0.65 (Keith's ranges)", "| ticker | price | low | high | rp |", "|---|---|---|---|---|"]
    L += [f"| {x['ticker']} | {x['price']} | {x['keith_low']:g} | {x['keith_high']:g} | {x['rp']} |"
          for x in s["etf_pro_shorts_high_in_range"]]
    L += ["", "## Desk watch list (triggers)", "| added | ticker | side | account | trigger | stop |", "|---|---|---|---|---|---|"]
    L += [f"| {w.get('added')} | {w.get('ticker')} | {w.get('side')} | {w.get('account')} | "
          f"{(w.get('trigger') or '').replace('|', '/')} | {(w.get('stop') or '').replace('|', '/')} |"
          for w in s["watch_list"]]
    L += ["", f"Inventory loaded: {s['inventory_counts']}", "", REVIEWER_BRIEF]
    return "\n".join(L)


RAW_BRIEF = """\
## What this is
Raw data only: Kris's positions and the market data feeds he uses (Hedgeye risk ranges,
ETF Pro ranges, Signal Strength / Position Monitor membership, TradingView indicator values,
SpotGamma levels, factor relative strength). No desk rules, calls or opinions are included,
on purpose: Kris wants an independent read. Kris makes every trade himself.
Questions to answer from this data alone: where is capital best rotated out of and into,
what looks wrong or risky in the book, and what is missing.
"""


def to_raw_md(s: dict, inv: dict) -> str:
    """Data only — none of the desk's rules, scanner calls, watch list or reviewer brief."""
    L = [f"# Portfolio + market data — {s['as_of']}", "", RAW_BRIEF,
         "## Accounts (book as of " + str(s.get("book_date")) + ")"]
    L += [f"- {a}: ${v:,}" for a, v in s["accounts"].items()]
    if s.get("data_as_of"):
        L += ["", "## Data as of (each feed's latest date in the bot)"]
        L += [f"- {k}: {v}" for k, v in s["data_as_of"].items()]
    L += ["", "## Positions", "Indicator columns are TradingView values at the prior close: MFR Trend "
          "cloud (lower/upper) and Vola2vola Trade. 'ETF Pro range' is Hedgeye's weekly range where one exists. "
          "'Range (source)' is the bot's working trade range for the name and where it came from.",
          "| account | ticker | name | qty | $ | % acct | price | Range (source) | ETF Pro range | MFR cloud lower | MFR cloud upper | Vola2vola Trade |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for b in s["book"]:
        L.append(f"| {b['account']} | {b['ticker']} | {b['what']} | {b['qty']:g} | {b['value']:,} | "
                 f"{b.get('pct_of_acct', '')} | {b.get('price', '')} | {b.get('range', '')} | {b.get('etf_range', '')} | "
                 f"{b.get('cloud_lo', '')} | {b.get('cloud_hi', '')} | {b.get('vola', '')} |")
    L += ["", "## Hedgeye risk ranges (latest)", "| ticker | trend | low | high | prev close |", "|---|---|---|---|---|"]
    L += [f"| {r['ticker']} | {r['trend']} | {r['low']:g} | {r['high']:g} | {r['prev']:g} |" for r in s["risk_ranges"]]
    if s["spotgamma"]:
        L += ["", f"## SpotGamma index levels ({s['spotgamma'][0]['date']})",
              "| sym | ref | vol trigger | zero gamma | call wall | put wall | risk pivot |", "|---|---|---|---|---|---|---|"]
        L += [f"| {g['sym']} | {g['ref']:g} | {g['vol_trigger']:g} | {g['zero_gamma']:g} | {g['call_wall']:g} | "
              f"{g['put_wall']:g} | {g['risk_pivot'] or ''} |" for g in s["spotgamma"]]
    L += ["", "## Factor / sector relative strength", "```", str(s.get("factor_board", "")), "```",
          "", "## Hedgeye inventory (list membership)",
          f"- Signal Strength longs ({len(inv['ss_long'])}): " + ", ".join(inv["ss_long"]),
          f"- Signal Strength shorts ({len(inv['ss_short'])}): " + ", ".join(inv["ss_short"]),
          f"- ETF Pro longs: " + ", ".join(inv["etf_long"]),
          f"- ETF Pro shorts: " + ", ".join(inv["etf_short"])]
    for bucket, names in sorted(inv["pm"].items()):
        L.append(f"- Position Monitor {bucket}: " + ", ".join(names))
    L += ["", "## ETF Pro weekly ranges", "| ticker | side | low | high | price |", "|---|---|---|---|---|"]
    L += [f"| {e['ticker']} | {e['side']} | {e['low']:g} | {e['high']:g} | {e.get('price', '')} |" for e in inv["etf_rows"]]
    return "\n".join(L)


def _inventory(s: dict) -> dict:
    import db_pg
    import yfinance as yf
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("select ticker, side from ss_roster_history where removed_on is null order by ticker")
        ss = cur.fetchall()
        cur.execute("select ticker, hedgeye_bucket_0629 from ticker_tags where hedgeye_bucket_0629 is not null order by 1")
        pm = cur.fetchall()
        cur.execute("""select distinct on (ticker) ticker, bias, range_low, range_high from hedgeye_etf_pro_ranges
                       where week_of >= current_date - 10 order by ticker, week_of desc""")
        etf = [r for r in cur.fetchall() if r[2] and r[3]]
    try:
        px = yf.download([e[0] for e in etf], period="5d", progress=False)["Close"].ffill().iloc[-1]
    except Exception:
        px = {}
    pmd: dict = {}
    for t, b in pm:
        pmd.setdefault(b, []).append(t)
    return {"ss_long": [t for t, sd in ss if sd == "long"], "ss_short": [t for t, sd in ss if sd == "short"],
            "pm": pmd, "etf_long": [e[0] for e in etf if e[1] == "long"],
            "etf_short": [e[0] for e in etf if e[1] == "short"],
            "etf_rows": [{"ticker": e[0], "side": e[1], "low": float(e[2]), "high": float(e[3]),
                          "price": round(float(px[e[0]]), 2) if e[0] in px else ""} for e in etf]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--raw", action="store_true", help="data only (no rules/calls) -> desk_snapshot_raw.md")
    a = ap.parse_args()
    s = build(scan=not (a.no_scan or a.raw))
    OUT.mkdir(parents=True, exist_ok=True)
    if a.raw:
        (OUT / "desk_snapshot_raw.md").write_text(to_raw_md(s, _inventory(s)), encoding="utf-8")
        print(f"wrote {OUT / 'desk_snapshot_raw.md'} ({len(s['book'])} positions)")
        return 0
    (OUT / "desk_snapshot.md").write_text(to_md(s), encoding="utf-8")
    (OUT / "desk_snapshot.json").write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT / 'desk_snapshot.md'} ({len(s['book'])} positions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
