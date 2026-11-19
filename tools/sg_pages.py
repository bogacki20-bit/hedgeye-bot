"""tools/sg_pages.py - load SpotGamma page captures into the bot (Kris 11/15/2026: "build the rest so we get all of it").

Files come from Kris's Chrome (capture desk):
  sg_page_*.json     tools/sg_page_capture.js on any dashboard page (Home, Indices x 6 symbols x 3 tabs, Tape Contract
                     Data, Equity Hub tickers, ...)
  sg_reports_*.json  tools/sg_report_capture.js on the Reports page (FlowPatrol, Opening Setup, Sunday Note text)

    railway run py -m tools.sg_pages --ingest-downloads      # every sg_page_* / sg_reports_* in Downloads
    railway run py -m tools.sg_pages --ingest <file>
    railway run py -m tools.sg_pages --status                 # what landed today, per page

Where it goes (migration 142):
  sg_index_daily   Home Index Levels per index per trade date: walls, zero gamma, vol trigger, Large Gamma 1-4,
                   Combo 1-4, ATM greeks, OI/volume, implied moves, futures offset, full level list, gamma curve
  sg_series        every other data series on the page, named by its fields (greeks by strike / expiry, level history,
                   intraday tilt, OI by expiry, absolute gamma, risk reversal, price bars, grids ...)
  sg_events        the events calendar
  sg_scanner_rows  scanner grid rows (squeeze / VRP / GARCH flags + the full record)
  sg_equity        full Equity Hub records (with 10-day history) whenever a page shows one
  sg_reports       report text
  sg_page_captures one row per capture
Raw files are gzipped into data/sg_pages/raw/<YYYYMMDD>/ (kept local, never committed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import shutil
import sys

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
RAW_DIR = os.path.join("data", "sg_pages", "raw")
LONG_KEEP = 400          # long time series: keep the newest rows in the table (the raw file keeps all)

# field signature -> series name (sorted field names joined with ',')
NAMES = {
    "call,call_delta,call_gamma,delta,gamma,id,percent_delta,percent_gamma,put,put_delta,put_gamma,strike": "greeks_by_strike",
    "call,call_delta,call_gamma,delta,expiration,gamma,id,percent_delta,percent_gamma,put,put_delta,put_gamma": "greeks_by_expiry",
    "call_wall_strike,max_g_strike,net_delta,net_gamma,put_wall_strike,trade_date,upx": "levels_history",
    "call,expiration,put": "oi_by_expiry",
    "delta_tilt,epoch_millis,gamma_tilt,upx": "tilt_intraday",
    "gamma,strike": "abs_gamma",
    "current_call,current_put,strike": "gamma_curve_calls_puts",
    "epoch_millis,rr": "risk_reversal_history",
    "close,datetime,high,low,open,volume": "price_bars",
    "underlying,val": "tape_top_list",
    "close,last_close,underlying": "tape_top_movers",
    "expiry,is_put,premium,strike,underlying": "tape_largest_trades",
    "color,currentPrice,data,key,tradeDate": "composite_chart",
    "all_oi,all_volume,epoch_millis,percent_total,rolling_oi_percentage,sym,zero_dte_oi,zero_dte_volume,zero_oi_percentage,zero_vol_percentage": "zero_dte_share",
}
# UI / framework objects the generic capture can't tell apart from data
JUNK = {"basename", "pathname", "idPrefix", "adornedStart", "fontSize", "firstRowIndex", "firstColumnIndex", "maxHeight",
        "--width", "overflowX", "height", "cellCheckbox", "rowReorderCellContainer", "tag", "_className", "annotationFlags",
        "isReady", "vix_econ", "items", "value", "leftIdx", "theme", "disabledAlertCategories", "soundDisabled"}


# keys charting libraries add to each data point (drawn shape, tooltip); dropped before naming / storing
CHART_KEYS = {"x", "y", "height", "width", "value", "payload", "background", "tooltipPayload", "tooltipPosition",
              "cx", "cy", "radius", "name", "fill", "stroke"}


def _clean(rows: list) -> list:
    if rows and isinstance(rows[0], dict) and {"payload", "tooltipPayload"} & set(rows[0]):
        return [{k: x for k, x in r.items() if k not in CHART_KEYS} for r in rows]
    return rows


def _sig(rec: dict) -> str:
    return ",".join(sorted(rec.keys()))


RULES = [   # (fields that must all be present, name) when the exact signature isn't in NAMES
    ({"strike", "call_gamma", "put_gamma"}, "greeks_by_strike"),
    ({"strike", "call_gamma"}, "greeks_by_strike_calls"),
    ({"strike", "put_gamma"}, "greeks_by_strike_puts"),
    ({"expiration", "call_gamma"}, "greeks_by_expiry"),
    ({"next_exp", "strike"}, "next_exp_gamma"),
    ({"moneyness", "delta"}, "skew_curve"),
    ({"contractString", "delta_notional"}, "tape_contracts"),
    ({"activity_factor", "atm_iv30"}, "equity_history"),
    ({"current_call", "current_put", "strike"}, "gamma_curve_calls_puts"),
]


def _name(first: dict) -> str:
    sig = _sig(first)
    if sig in NAMES:
        return NAMES[sig]
    ks = set(first)
    for need, name in RULES:
        if need <= ks:
            return name
    return "sig_" + hashlib.md5(sig.encode()).hexdigest()[:6] + "_" + "_".join(sorted(first)[:3])


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _date(v) -> str | None:
    return str(v)[:10] if v else None


def _jsonish(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def classify(d: dict) -> dict:
    """Pure: split one sg_page capture into rows for each table."""
    out = {"index": [], "series": [], "events": [], "scanner": [], "equity": []}
    cap = d["captured_at"]
    day = (d.get("stamp") or "")[:8]
    cdate = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else cap[:10]
    sym = d.get("sym") or ""
    page = d.get("page") or "home"
    tab = d.get("tab") or ""
    named = set()

    seen_rows = set()

    def series(name, rows):
        h = hashlib.md5(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
        if h in seen_rows:                       # the same data drawn twice on a page
            return
        seen_rows.add(h)
        key = name if name not in named else f"{name}_{len(named)}"
        named.add(key)
        if len(rows) > LONG_KEEP * 2 and any(k in rows[0] for k in ("trade_date", "epoch_millis", "datetime", "date")):
            rows = rows[-LONG_KEEP:]
        out["series"].append({"captured_date": cdate, "page": page + (f":{tab}" if tab else ""), "sym": sym,
                              "name": key, "n": len(rows), "data": rows, "captured_at": cap})

    def equity(rec):
        if rec.get("sym") and (rec.get("trade_date") or rec.get("quote_date")):
            out["equity"].append({"trade_date": _date(rec.get("trade_date") or rec.get("quote_date")),
                                  "sym": rec["sym"], "data": rec, "captured_at": cap})

    for key, v in (d.get("states") or {}).items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            v = _clean([x for x in v if isinstance(x, dict)])
            if not v:
                continue
            first = v[0]
            ks = set(first)
            if {"sym", "cws", "pws", "upx"} <= ks and len(v) <= 50:     # Equity Hub record(s)
                for r in v:
                    equity(r)
                continue
            if ks & JUNK:
                continue
            if {"sym", "L1", "C1", "callwallstrike"} <= ks:          # home Index Levels
                for r in v:
                    out["index"].append(r | {"_cap": cap})
                continue
            if {"event", "country", "date"} <= ks:
                out["events"].extend(v)
                continue
            nums = sum(isinstance(x, (int, float)) and not isinstance(x, bool) for x in first.values())
            if len(v) == 1 and nums < 5 and not any(isinstance(x, list) and len(x) > 2 for x in first.values()):
                continue                         # a single settings / UI object, not data
            if nums < 1 and len(v) < 5:
                continue
            series(_name(first), v)
        elif isinstance(v, dict):
            ks = set(v)
            if {"sym", "cws", "pws", "upx", "hist_trade_dates"} <= ks:   # a full Equity Hub record
                equity(v)
                continue
            rows = v.get("rows")
            if isinstance(rows, dict) and isinstance(rows.get("dataRowIdToModelLookup"), dict):   # a data grid
                recs = list(rows["dataRowIdToModelLookup"].values())
                if recs and isinstance(recs[0], dict):
                    if {"squeeze_scanner", "vrp_scanner"} & set(recs[0]):
                        out["scanner"].extend(recs)
                    else:
                        series(_name(recs[0]) if not _name(recs[0]).startswith("sig_") else "grid_" + "_".join(sorted(recs[0])[:3]), recs)
                continue
            if ks & JUNK:
                continue
            flowish = all(isinstance(x, dict) and {"put", "call"} <= set(x) for x in v.values() if isinstance(x, dict))
            if flowish and any(isinstance(x, dict) for x in v.values()):
                series("flow_sum", [v])
    for g in d.get("grids") or []:
        rows = g.get("rows") or []
        if rows and isinstance(rows[0], dict):
            if {"squeeze_scanner", "vrp_scanner"} & set(rows[0]):
                out["scanner"].extend(rows)
            else:
                series(_name(rows[0]) if not _name(rows[0]).startswith("sig_") else "grid_" + "_".join(sorted(rows[0])[:3]), rows)
    return out


INDEX_COLS = [("upx", "upx"), ("zero_gamma", "zero_g_strike"), ("vol_trigger", "max_g_strike"),
              ("call_wall", "callwallstrike"), ("put_wall", "putwallstrike"), ("call_wall_gamma", "callwallgam"),
              ("put_wall_gamma", "putwallgam"), ("abs_gamma_strike", "topabs_strike"),
              ("l1", "L1"), ("l2", "L2"), ("l3", "L3"), ("l4", "L4"), ("c1", "C1"), ("c2", "C2"), ("c3", "C3"),
              ("c4", "C4"), ("atm_gamma_calls", "atm_g_calls"), ("atm_gamma_puts", "atm_g_puts"),
              ("atm_delta_calls", "atm_d_calls"), ("atm_delta_puts", "atm_d_puts"), ("itm_delta", "itm_d"),
              ("atm_theta", "atm_Theta"), ("atm_vega", "atm_Vega"), ("atm_gamma_calls_n", "atm_g_c_n"),
              ("atm_gamma_puts_n", "atm_g_p_n"), ("risk_reversal", "rr"), ("calls_oi", "calls_OI"),
              ("puts_oi", "puts_OI"), ("calls_vol", "calls_vol"), ("puts_vol", "puts_vol"), ("sig", "sig"),
              ("sig5", "sig5"), ("gamma_notional", "gamma_not"), ("futures_diff", "futuresDiff")]


def _store(conn, d: dict, fname: str) -> dict:
    from psycopg2.extras import Json
    c = classify(d)
    cur = conn.cursor()
    for r in c["index"]:
        cols = ["trade_date", "sym"] + [a for a, _ in INDEX_COLS] + ["futures_sym", "levels", "gamma_curve", "captured_at"]
        vals = ([_date(r.get("keyLevelTradeDate") or r.get("trade_date")), r["sym"]] +
                [_num(r.get(b)) for _, b in INDEX_COLS] +
                [r.get("futuresSym"), Json(_jsonish(r.get("levels_with_pct"))),
                 Json({k: _jsonish(r.get(k)) for k in ("strike_list", "current_list", "next_exp_list")}), r["_cap"]])
        cur.execute(f"INSERT INTO sg_index_daily ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
                    "ON CONFLICT (trade_date, sym) DO UPDATE SET " +
                    ",".join(f"{x}=EXCLUDED.{x}" for x in cols[2:]), vals)
    for s in c["series"]:
        cur.execute("INSERT INTO sg_series (captured_date, page, sym, name, n, data, captured_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (captured_date, page, sym, name) DO UPDATE SET "
                    "n=EXCLUDED.n, data=EXCLUDED.data, captured_at=EXCLUDED.captured_at",
                    (s["captured_date"], s["page"], s["sym"], s["name"], s["n"], Json(s["data"]), s["captured_at"]))
    for e in c["events"]:
        if not e.get("date") or not e.get("event"):
            continue
        cur.execute("INSERT INTO sg_events (event_at, country, event, currency, previous, estimate, actual, change, "
                    "change_pct, impact, unit) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (event_at, country, event) DO UPDATE SET previous=EXCLUDED.previous, "
                    "estimate=EXCLUDED.estimate, actual=EXCLUDED.actual, change=EXCLUDED.change, "
                    "change_pct=EXCLUDED.change_pct, impact=EXCLUDED.impact, updated_at=now()",
                    (e["date"], e.get("country") or "", e["event"], e.get("currency"), _num(e.get("previous")),
                     _num(e.get("estimate")), _num(e.get("actual")), _num(e.get("change")),
                     _num(e.get("changePercentage")), str(e.get("impact") or ""), e.get("unit")))
    cdate = c["series"][0]["captured_date"] if c["series"] else d["captured_at"][:10]
    for r in c["scanner"]:
        if not r.get("sym"):
            continue
        cur.execute("INSERT INTO sg_scanner_rows (captured_date, sym, squeeze, vrp, vrp_high, garch, data, captured_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (captured_date, sym) DO UPDATE SET "
                    "squeeze=EXCLUDED.squeeze, vrp=EXCLUDED.vrp, vrp_high=EXCLUDED.vrp_high, garch=EXCLUDED.garch, "
                    "data=EXCLUDED.data, captured_at=EXCLUDED.captured_at",
                    (cdate, r["sym"], bool(r.get("squeeze_scanner")), bool(r.get("vrp_scanner")),
                     bool(r.get("vrp_scanner_high")), bool(r.get("garch_scanner")), Json(r), d["captured_at"]))
    for q in c["equity"]:
        if not q["trade_date"]:
            continue
        cur.execute("INSERT INTO sg_equity (trade_date, sym, data, captured_at) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (trade_date, sym) DO UPDATE SET data=EXCLUDED.data, captured_at=EXCLUDED.captured_at",
                    (q["trade_date"], q["sym"], Json(q["data"]), q["captured_at"]))
    cur.execute("INSERT INTO sg_page_captures (captured_at, page, sym, tab, file, n_states, n_grids, n_series, bytes) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (d["captured_at"], d.get("page") or "home", d.get("sym") or "", d.get("tab") or "", fname,
                 len(d.get("states") or {}), len(d.get("grids") or []), len(c["series"]),
                 len(json.dumps(d))))
    conn.commit()
    return {"file": fname, "index": len(c["index"]), "series": len(c["series"]), "events": len(c["events"]),
            "scanner": len(c["scanner"]), "equity": len(c["equity"])}


def _store_reports(conn, d: dict, fname: str) -> dict:
    cur = conn.cursor()
    n = 0
    notes: set = set()
    for r in d.get("reports") or []:
        if not r.get("text") or len(r["text"]) < 200:
            continue
        r = r | {"text": r["text"].replace(chr(0), "")}       # PDF text layers can carry NUL bytes
        cur.execute("INSERT INTO sg_reports (report_date, kind, title, url, pages, text, captured_at) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (report_date, kind) DO UPDATE SET title=EXCLUDED.title, "
                    # keep a local PDF path the capture desk stored in url (tools/sg_reports.py); keep the longer text
                    "url=CASE WHEN sg_reports.url ~ '^(data|[A-Za-z]:)' THEN sg_reports.url ELSE EXCLUDED.url END, "
                    "pages=COALESCE(EXCLUDED.pages, sg_reports.pages), "
                    "text=CASE WHEN length(EXCLUDED.text) >= length(coalesce(sg_reports.text, '')) THEN EXCLUDED.text "
                    "ELSE sg_reports.text END, captured_at=EXCLUDED.captured_at",
                    (r["report_date"], r["kind"], r["title"], r.get("url"), r.get("pages"), r["text"], d["captured_at"]))
        n += 1
        if r["kind"] in ("AM Note", "PM Note"):
            notes.add(r["report_date"])
    conn.commit()
    out = {"file": fname, "reports": n}
    if notes:
        # 11/19: sg_index_levels straight from the note text (it sat at 11/13 until the capture desk hand-wrote
        # founders_note_*.md files). Own try: a parse problem never loses the stored report.
        try:
            from tools import sg_index_levels
            out["index_levels"] = sg_index_levels.ingest_from_reports(sorted(notes), conn)
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            out["index_levels_error"] = str(e)[:120]
    return out


def ingest(path: str) -> dict:
    import db_pg
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    name = os.path.basename(path)
    with db_pg.get_conn() as conn:
        if name.startswith("sg_reports_"):
            return _store_reports(conn, d, name)
        if "captures" in d:                       # tools/sg_daily_capture.js: many pages in one file
            tot: dict = {"file": name, "pages": 0}
            for one in d["captures"]:
                r = _store(conn, one, name)
                tot["pages"] += 1
                for k in ("index", "series", "events", "scanner", "equity"):
                    tot[k] = tot.get(k, 0) + r[k]
            return tot
        # a reports page capture also carries the open report's text
        r = _store(conn, d, name)
        if d.get("page") == "reports" and d.get("text"):
            import re
            from urllib.parse import unquote
            m = re.search(r"(FlowPatrol|Opening Setup|Sunday Note) (\d{4}-\d{2}-\d{2})", unquote(d.get("url", "")))
            if m:
                _store_reports(conn, {"captured_at": d["captured_at"], "reports": [
                    {"title": m.group(0), "kind": m.group(1), "report_date": m.group(2), "url": d["url"],
                     "pages": None, "text": d["text"]}]}, name)
        return r


def ingest_downloads() -> list[dict]:
    out = []
    files = sorted(glob.glob(os.path.join(DOWNLOADS, "sg_page_*.json")) + glob.glob(os.path.join(DOWNLOADS, "sg_reports_*.json")))
    for p in files:
        try:
            r = ingest(p)
        except Exception as e:  # noqa: BLE001 - keep the file, report, go on
            out.append({"file": os.path.basename(p), "error": str(e)[:160]})
            continue
        stamp = os.path.basename(p).rsplit("_", 2)[-2]
        dest = os.path.join(RAW_DIR, stamp)
        os.makedirs(dest, exist_ok=True)
        with open(p, "rb") as src, gzip.open(os.path.join(dest, os.path.basename(p) + ".gz"), "wb") as gz:
            shutil.copyfileobj(src, gz)
        os.remove(p)
        out.append(r)
    return out


def book_syms() -> list[str]:
    """US-listed stocks/ETFs in the latest book snapshot (no options, cash or money funds), for the daily runner."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT symbol, sum(abs(market_value)) FROM book_positions WHERE snapshot_date = "
                    "(SELECT max(snapshot_date) FROM book_positions) AND asset_class <> 'cash' "
                    "AND position(' ' in symbol) = 0 AND position('*' in symbol) = 0 AND symbol !~ '^-' "
                    "AND symbol NOT IN ('SPAXX','FDRXX','CORE','BUXX') GROUP BY 1 ORDER BY 2 DESC")
        return [r[0] for r in cur.fetchall() if r[0].isalpha() and len(r[0]) <= 5]


GAMMA_YF = {"SPX": "^GSPC", "SPY": "SPY", "NDX": "^NDX", "QQQ": "QQQ", "RUT": "^RUT", "IWM": "IWM"}


def gamma() -> str:
    """Rule card (Kris 11/15): SPX vs SpotGamma's Volatility Trigger sets the step size; put wall is not a floor;
    call wall = index-ETF trim zone; all levels join the backboard. Live price vs the latest sg_index_daily row."""
    import db_pg
    import yfinance as yf
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ON (sym) sym, trade_date, vol_trigger, zero_gamma, call_wall, put_wall, "
                    "l1, l2, l3, l4, c1, c2, c3, c4, upx FROM sg_index_daily ORDER BY sym, trade_date DESC")
        rows = {r[0]: r[1:] for r in cur.fetchall()}
    if "SPX" not in rows:
        return "no SpotGamma index levels yet (capture desk: daily run)"
    live = {}
    for sym, t in GAMMA_YF.items():
        try:
            live[sym] = float(yf.Ticker(t).fast_info["last_price"])
        except Exception:  # noqa: BLE001 - fall back to SpotGamma's own reference price
            live[sym] = float(rows[sym][-1]) if sym in rows and rows[sym][-1] else None
    f = lambda x: f"{float(x):,.0f}" if x is not None and abs(float(x)) >= 1000 else (f"{float(x):,.2f}" if x is not None else "-")
    out = []
    for sym in ("SPX", "SPY", "NDX", "QQQ", "RUT", "IWM"):
        if sym not in rows:
            continue
        td, vt, zg, cw, pw, l1, l2, l3, l4, c1, c2, c3, c4, upx = rows[sym]
        px = live.get(sym)
        reg = "NEG" if px is not None and vt is not None and px < float(vt) else "POS"
        flags = []
        if px is not None and pw is not None and px <= float(pw):
            flags.append("AT/BELOW PUT WALL: reload waits until back above")
        if px is not None and cw is not None and 0 <= float(cw) / px - 1 <= 0.01:
            flags.append("within 1% under CALL WALL: index-ETF trims 50 bp")
        out.append(f"{sym} {f(px)} | gamma {reg} (VT {f(vt)}, zero {f(zg)}) | call wall {f(cw)} | put wall {f(pw)} | "
                   f"LG {f(l1)}/{f(l2)}/{f(l3)}/{f(l4)} | combo {f(c1)}/{f(c2)}/{f(c3)}/{f(c4)} | levels from {td}"
                   + ("".join(" | " + x for x in flags)))
    spx = rows["SPX"]
    reg = "neg" if live.get("SPX") is not None and live["SPX"] < float(spx[1]) else "pos"
    out.insert(0, f"Gamma: {reg} (SPX {f(live.get('SPX'))} vs VT {f(spx[1])})" +
               ("  -> half-size steps on new positions and adds" if reg == "neg" else "  -> normal steps; RANGE setup"))
    return "\n".join(out)


def status() -> str:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT page, sym, tab, max(captured_at) FROM sg_page_captures "
                    "WHERE captured_at >= now() - interval '36 hours' GROUP BY 1,2,3 ORDER BY 1,2,3")
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        lines = [f"{p:10} {s:6} {t:15} {m.astimezone(et):%m/%d %H:%M} ET" for p, s, t, m in cur.fetchall()]
        cur.execute("SELECT kind, max(report_date), count(*) FROM sg_reports GROUP BY 1 ORDER BY 1")
        lines += [f"report {k}: latest {d}, {n} stored" for k, d, n in cur.fetchall()]
        cur.execute("SELECT max(trade_date), count(DISTINCT sym) FROM sg_index_daily")
        lines.append("index levels: latest %s, %s symbols" % cur.fetchone())
    return "\n".join(lines) or "nothing captured in the last 36 hours"


def _cli() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ingest")
    g.add_argument("--ingest-downloads", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--book-syms", action="store_true")
    g.add_argument("--gamma", action="store_true")
    a = ap.parse_args()
    if a.ingest:
        print(ingest(a.ingest))
    elif a.ingest_downloads:
        for r in ingest_downloads():
            print(r)
    elif a.gamma:
        print(gamma())
    elif a.book_syms:
        print("window.__sgDailySyms = " + json.dumps(book_syms()) + ";")
    else:
        print(status())
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(_cli())
