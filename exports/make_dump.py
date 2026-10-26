"""One-off: export the last week of bot data + portfolio for outside analysis.
Structured data only (signals, levels, positions, trades). No email bodies,
no research prose, no personal spending, account numbers masked."""
import csv, os, sys, zipfile, json, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_pg

START, END = "2026-10-17", "2026-10-24"   # END exclusive
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"bot_dump_{START}_to_2026-10-23")
os.makedirs(OUT, exist_ok=True)

TABLES = {  # table: (date column or None for full snapshot, description)
 "book_positions": ("snapshot_date", "Daily Fidelity position snapshots (all 3 accounts)"),
 "book_activity": ("run_date", "Fidelity trades/activity (buys, sells, shorts, covers)"),
 "fills": (None, "Lot-level fills ledger"),
 "outcomes_log": (None, "Realized round trips (FIFO)"),
 "v_screener": (None, "Current screener: price, range_low/high, range_pos, trend_dir, TV trade/trend lines, Hedgeye bucket"),
 "tv_levels": ("bar_date", "Daily TradingView levels: MFR trend band, Vola2vola lines, ranges, close"),
 "mfr_snapshots": ("snapshot_date", "Daily MFR range/trend/momentum per ticker"),
 "ss_roster_history": (None, "Hedgeye Signal Strength long/short roster with add/remove dates"),
 "ticker_tags": (None, "Ticker -> Hedgeye Position Monitor bucket/group"),
 "hedgeye_signal_changes": ("signal_date", "Hedgeye trend/trade signal changes"),
 "hedgeye_risk_ranges": (None, "Hedgeye risk ranges (filtered to week)"),
 "hedgeye_etf_pro_ranges": (None, "ETF Pro ranges/bias (filtered to week)"),
 "hedgeye_portfolio_solutions": (None, "Portfolio Solutions ranks (filtered to week)"),
 "hedgeye_rerank": (None, "Daily ETF re-rank (filtered to week)"),
 "hedgeye_rta": (None, "Real-time alerts, structured (filtered to week)"),
 "t1a_snapshots": (None, "Tier1 Alpha MSR parsed fields (filtered to week)"),
 "tms_decks": (None, "Macro Show deck parsed fields (filtered to week)"),
 "hedgeye_quad_nowcast": (None, "Quad nowcast (filtered to week)"),
 "quad_monthly": (None, "Quad history (monthly)"),
 "alerts_fired": (None, "Bot alerts fired (filtered to week)"),
 "book_alerts_fired": (None, "Book trade/trend-break alerts (filtered to week)"),
 "spotgamma_snapshots": (None, "SpotGamma EquityHub snapshots (filtered to week)"),
 "energy_cracks_daily": (None, "Crack spreads daily (filtered to week)"),
 "live_quotes": (None, "Latest quotes"),
}
SKIP_COL_HINTS = ("html", "body", "raw_text", "full_text", "transcript", "prose", "commentary_text", "full_payload", "process_text", "top3", "callouts", "extra_pages", "prompt_context", "recommendation_text", "hedgeye_context", "spotgamma_context")
SKIP_EXACT = ("raw", "note")
DATE_TYPES = ("date", "timestamp without time zone", "timestamp with time zone")

def cols(cur, t):
    cur.execute("select column_name, data_type from information_schema.columns where table_name=%s order by ordinal_position", (t,))
    return cur.fetchall()

def mask(v):
    s = str(v)
    return ("****" + s[-4:]) if len(s) > 4 else s

readme = [f"# Hedgeye bot data dump {START} .. 2026-10-23", "",
          "Structured data only. Email bodies / research prose / personal spending excluded; account numbers masked to last 4.",
          "Positions are long (+) / short (-) market values. range_pos = (price - range_low)/(range_high - range_low).",
          "TV lines: trade = Vola2vola upper line; trend = MFR Trend band (bot uses lower edge; backtest suggests midpoint).", "",
          "| file | rows | description |", "|---|---|---|"]
with db_pg.get_conn() as c:
    cur = c.cursor()
    for t, (dcol, desc) in TABLES.items():
        try:
            cc = cols(cur, t)
            if not cc:
                continue
            keep = [n for n, ty in cc if not any(h in n.lower() for h in SKIP_COL_HINTS) and n.lower() not in SKIP_EXACT]
            if dcol is None:  # pick first date-like col for week filter, unless it's a full-snapshot table
                full = t in ("v_screener", "ss_roster_history", "ticker_tags", "quad_monthly", "live_quotes", "fills", "outcomes_log")
                dcol = None if full else next((n for n, ty in cc if ty in DATE_TYPES and n in keep), None)
            sel = ", ".join(f'"{k}"' for k in keep)
            if dcol:
                cur.execute(f'select {sel} from {t} where "{dcol}" >= %s and "{dcol}" < %s', (START, END))
            else:
                cur.execute(f"select {sel} from {t}")
            rows = cur.fetchall()
            mi = [i for i, k in enumerate(keep) if k in ("account_number", "account")]
            path = os.path.join(OUT, f"{t}.csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh); w.writerow(keep)
                for r in rows:
                    r = list(r)
                    for i in mi:
                        if r[i] is not None and t != "book_activity" or (t == "book_activity" and keep[i] == "account_number"):
                            r[i] = mask(r[i]) if r[i] is not None else r[i]
                    w.writerow([json.dumps(x, default=str) if isinstance(x, (dict, list)) else x for x in r])
            readme.append(f"| {t}.csv | {len(rows)} | {desc}{' — filtered on ' + dcol if dcol else ''} |")
            print(f"{t:30} {len(rows):7} rows")
        except Exception as e:
            c.rollback(); print(f"{t:30} SKIP {e.__class__.__name__}: {str(e)[:80]}")
open(os.path.join(OUT, "README.md"), "w", encoding="utf-8").write("\n".join(readme) + "\n")
z = OUT + ".zip"
with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in os.listdir(OUT):
        zf.write(os.path.join(OUT, f), f)
print("ZIP", z, round(os.path.getsize(z) / 1e6, 2), "MB")
