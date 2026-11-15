"""tools/global_macro_morning.py - the morning global macro read (Kris 2026-11-15).

"a report in the morning for the global macro: what bonds are doing in the world, what the
indexes are doing, so we have a solid read of the global market."

Two layers per row:
  move    : Yahoo daily bars - today's bar where the market is open or has closed (Asia closed,
            Europe live, US futures live), else the last close. chg = vs the prior bar; 1w / 1m.
            Yields show the level and the change in bp.
  context : MFR at the last US close (EOD) for the instrument we trade it with (country ETF,
            bond ETF, FX ETF, commodity ETF): position vs the MFR Trend cloud (the card's Trend),
            vs the Vola2vola Trade line, and range position rp in the MFR risk range.
  read    : same verdicts as tools/macro_sunday.py, on the cloud:
            above cloud + rp <= 0.40 = BUY DIP | below cloud + rp >= 0.60 = SHORT BOUNCE
            above = long, below = short, inside = -
Bond ETF prices move opposite yields: a bond ETF down = yields up.

Usage: py -m tools.global_macro_morning [--send] [--no-save]
  --send     Telegram: headline message + the full report as one .txt
Writes outputs/global_macro/global_macro_<date>.md and the global_macro_morning table.
Read-only on everything else. Not on the card: a read, not an action list.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _now():
    return datetime.now(ET).replace(tzinfo=None)

warnings.filterwarnings("ignore")
log = logging.getLogger("global_macro_morning")

# (label, yahoo symbol, MFR symbol for context, kind)  kind: px | yld (yield, % level)
BOARD = [
    ("US futures & vol", [
        ("S&P 500 fut", "ES=F", "SPY", "px"), ("Nasdaq 100 fut", "NQ=F", "QQQ", "px"),
        ("Russell 2000 fut", "RTY=F", "IWM", "px"), ("Dow fut", "YM=F", "DIA", "px"),
        ("VIX", "^VIX", None, "px"),
    ]),
    ("Asia (closed)", [
        ("Japan Nikkei", "^N225", "EWJ", "px"), ("Hong Kong HSI", "^HSI", "EWH", "px"),
        ("China Shanghai", "000001.SS", "FXI", "px"), ("Korea Kospi", "^KS11", "EWY", "px"),
        ("Taiwan Taiex", "^TWII", "EWT", "px"), ("Australia ASX200", "^AXJO", "EWA", "px"),
        ("India Nifty", "^NSEI", "INDA", "px"),
    ]),
    ("Europe (live)", [
        ("Euro Stoxx 50", "^STOXX50E", "VGK", "px"), ("Germany DAX", "^GDAXI", "EWG", "px"),
        ("France CAC", "^FCHI", "EWQ", "px"), ("UK FTSE 100", "^FTSE", "EWU", "px"),
        ("Italy MIB", "FTSEMIB.MI", "EWI", "px"), ("Spain IBEX", "^IBEX", "EWP", "px"),
        ("Swiss SMI", "^SSMI", "EWL", "px"),
    ]),
    ("Americas (last close)", [
        ("Canada TSX", "^GSPTSE", "EWC", "px"), ("Brazil Bovespa", "^BVSP", "EWZ", "px"),
        ("Mexico IPC", "^MXX", "EWW", "px"),
    ]),
    ("US Treasury yields", [
        ("UST 3m", "^IRX", None, "yld"), ("UST 5y", "^FVX", None, "yld"),
        ("UST 10y", "^TNX", None, "yld"), ("UST 30y", "^TYX", None, "yld"),
    ]),
    ("Sovereign bonds (price: down = yields up)", [
        ("UST 20y+ TLT", "TLT", "TLT", "px"), ("UST 7-10y IEF", "IEF", "IEF", "px"),
        ("UST 1-3y SHY", "SHY", "SHY", "px"), ("US TIPS TIP", "TIP", "TIP", "px"),
        ("Germany govt", "EXX6.DE", "EXX6.DE", "px"), ("Euro govt", "IBGL.L", "IBGL.L", "px"),
        ("UK gilts", "IGLT.L", "IGLT.L", "px"), ("Canada govt", "XGB.TO", "XGB.TO", "px"),
        ("Australia bonds", "IAF.AX", "IAF.AX", "px"), ("Global ex-US", "BNDX", "BNDX", "px"),
        ("EM USD debt", "EMB", "EMB", "px"), ("EM local debt", "EMLC", "EMLC", "px"),
    ]),
    ("Credit (price)", [
        ("US high yield", "HYG", "HYG", "px"), ("US inv grade", "LQD", "LQD", "px"),
        ("Euro high yield", "IHYG.L", "IHYG.L", "px"), ("EM USD govt", "VDET.L", "VDET.L", "px"),
    ]),
    ("FX", [
        ("Dollar DXY", "DX-Y.NYB", "UUP", "px"), ("EUR/USD", "EURUSD=X", "FXE", "px"),
        ("USD/JPY (up=yen weak)", "JPY=X", "FXY", "px"), ("GBP/USD", "GBPUSD=X", "FXB", "px"),
        ("USD/CNY", "CNY=X", None, "px"), ("AUD/USD", "AUDUSD=X", "FXA", "px"),
        ("USD/CAD", "CAD=X", "FXC", "px"), ("USD/CHF", "CHF=X", "FXF", "px"),
        ("USD/MXN", "MXN=X", None, "px"), ("Bitcoin", "BTC-USD", "IBIT", "px"),
    ]),
    ("Commodities", [
        ("WTI crude", "CL=F", "USO", "px"), ("Brent crude", "BZ=F", "BNO", "px"),
        ("Natural gas", "NG=F", "UNG", "px"), ("Gold", "GC=F", "GLD", "px"),
        ("Silver", "SI=F", "SLV", "px"), ("Copper", "HG=F", "CPER", "px"),
        ("Corn", "ZC=F", "CORN", "px"), ("Wheat", "ZW=F", "WEAT", "px"),
        ("Soybeans", "ZS=F", "SOYB", "px"), ("Coffee", "KC=F", "COFF.L", "px"),
        ("Cocoa", "CC=F", "COCO.L", "px"), ("Sugar", "SB=F", "CANE", "px"),
        ("Cotton", "CT=F", "COTN.L", "px"), ("Broad commods", "DBC", "DBC", "px"),
    ]),
]


def _closes(symbols):
    import pandas as pd
    import yfinance as yf
    px = yf.download(sorted(set(symbols)), period="3mo", interval="1d", auto_adjust=False,
                     progress=False, group_by="column", threads=True)
    if px is None or px.empty:
        return pd.DataFrame()
    c = px["Close"] if "Close" in px else px
    if isinstance(c, pd.Series):
        c = c.to_frame(symbols[0])
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c


def _mfr_context(tickers):
    import db_pg
    import psycopg2
    db_pg._load_dotenv_fallback()
    conn = psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn())
    cur = conn.cursor()
    out = {}
    cur.execute("""SELECT DISTINCT ON (ticker) ticker, snapshot_date, price, range_low, range_high, momentum_signal
                   FROM mfr_snapshots WHERE ticker = ANY(%s) ORDER BY ticker, snapshot_date DESC""", (list(tickers),))
    for tk, d, p, lo, hi, mom in cur.fetchall():
        out[tk] = {"date": d, "price": _f(p), "lo": _f(lo), "hi": _f(hi), "mom": mom}
    cur.execute("""SELECT ticker, bar_date, vola_trade, trend_bull, trend_bear FROM v_mfr_indicators_latest
                   WHERE ticker = ANY(%s)""", (list(tickers),))
    for tk, d, tr, bull, bear in cur.fetchall():
        out.setdefault(tk, {}).update({"bar": d, "trade": _f(tr), "bull": _f(bull), "bear": _f(bear)})
    sector = None
    try:
        cur.execute("SELECT bar_date, avg10, avg21 FROM mfr_sector_corr ORDER BY bar_date DESC LIMIT 250")
        rows = cur.fetchall()
        if rows:
            now = _f(rows[0][1])
            hist = [_f(r[1]) for r in rows if r[1] is not None]
            sector = (rows[0][0], now, _f(rows[0][2]), round(100 * sum(h <= now for h in hist) / len(hist)))
    except Exception:
        conn.rollback()
    conn.close()
    return out, sector


def _f(v):
    return float(v) if v is not None else None


def _ctx(m):
    """(cloud, trade, rp, read) from an MFR context dict at the last close."""
    if not m or m.get("price") is None:
        return "", "", None, ""
    p = m["price"]
    cloud = ""
    if m.get("bull") is not None and m.get("bear") is not None:
        top, bot = max(m["bull"], m["bear"]), min(m["bull"], m["bear"])
        cloud = "above" if p > top else "below" if p < bot else "in"
    trade = "" if m.get("trade") is None else ("over" if p > m["trade"] else "under")
    rp = None
    if m.get("lo") is not None and m.get("hi") is not None and m["hi"] > m["lo"]:
        rp = (p - m["lo"]) / (m["hi"] - m["lo"])
    read = ""
    if cloud == "above":
        read = "BUY DIP" if rp is not None and rp <= 0.40 else "long"
    elif cloud == "below":
        read = "SHORT BOUNCE" if rp is not None and rp >= 0.60 else "short"
    elif cloud == "in":
        read = "-"
    return cloud, trade, rp, read


def build():
    import pandas as pd
    syms = [r[1] for _, rows in BOARD for r in rows]
    mfr_syms = {r[2] for _, rows in BOARD for r in rows if r[2]}
    closes = _closes(syms + sorted(mfr_syms))
    ctx, sector = _mfr_context(mfr_syms)
    today = pd.Timestamp(_now().date())
    # Names imported after the last MFR snapshot have indicators but no snapshot price yet:
    # use Yahoo's last completed close so the cloud / Trade read still works (no rp without a range).
    for tk in mfr_syms:
        m = ctx.setdefault(tk, {})
        if m.get("price") is None and tk in closes.columns:
            prior = closes[tk].dropna()
            prior = prior[prior.index < today]
            if len(prior):
                m["price"] = float(prior.iloc[-1])
    data = []
    for sec, rows in BOARD:
        for label, ysym, msym, kind in rows:
            s = closes[ysym].dropna() if ysym in closes.columns else pd.Series(dtype=float)
            rec = {"sec": sec, "label": label, "y": ysym, "m": msym, "kind": kind}
            if len(s) >= 2:
                last, prev = float(s.iloc[-1]), float(s.iloc[-2])
                rec.update(last=last, bar=s.index[-1], live=s.index[-1] >= today)
                w = float(s.iloc[-6]) if len(s) >= 6 else None
                mo = float(s.iloc[-22]) if len(s) >= 22 else None
                if kind == "yld":
                    rec.update(chg=(last - prev) * 100, w=(last - w) * 100 if w else None,
                               mo=(last - mo) * 100 if mo else None)
                else:
                    rec.update(chg=100 * (last / prev - 1), w=100 * (last / w - 1) if w else None,
                               mo=100 * (last / mo - 1) if mo else None)
            cloud, trade, rp, read = _ctx(ctx.get(msym)) if msym else ("", "", None, "")
            rec.update(cloud=cloud, trade=trade, rp=rp, read=read)
            data.append(rec)
    mfr_date = max((v.get("bar") or v.get("date") for v in ctx.values() if v.get("bar") or v.get("date")), default=None)
    return data, sector, mfr_date


def _n(v, d=1, sign=True):
    if v is None:
        return "--"
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


def render(data, sector, mfr_date):
    now = _now()
    by = lambda sec: [r for r in data if r["sec"] == sec and r.get("chg") is not None]
    get = {r["label"]: r for r in data}

    def mv(label, d=1):
        r = get.get(label)
        if not r or r.get("chg") is None:
            return f"{label.split(' ')[0]} --"
        if r["kind"] == "yld":
            return f"{r['last']:.2f}% ({r['chg']:+.0f}bp)"
        return f"{r['chg']:+.{d}f}%"

    def breadth(sec):
        rs = by(sec)
        up = sum(r["chg"] > 0 for r in rs)
        best = max(rs, key=lambda r: r["chg"], default=None)
        worst = min(rs, key=lambda r: r["chg"], default=None)
        avg = sum(r["chg"] for r in rs) / len(rs) if rs else 0
        bw = f"best {best['label']} {best['chg']:+.1f}, worst {worst['label']} {worst['chg']:+.1f}" if rs else ""
        return f"{up} up / {len(rs) - up} down, avg {avg:+.1f}% ({bw})"

    bonds = [r for r in by("Sovereign bonds (price: down = yields up)")]
    b_down = sum(r["chg"] < 0 for r in bonds)
    sb = [r for r in data if r["read"] == "SHORT BOUNCE"]
    bd = [r for r in data if r["read"] == "BUY DIP"]
    head = [
        f"GLOBAL MACRO {now:%a %m/%d %H:%M} ET",
        f"Asia: {breadth('Asia (closed)')}",
        f"Europe live: {breadth('Europe (live)')}",
        f"US futs: ES {mv('S&P 500 fut')}, NQ {mv('Nasdaq 100 fut')}, RTY {mv('Russell 2000 fut')}; VIX {mv('VIX')}",
        f"UST: 10y {mv('UST 10y')}, 30y {mv('UST 30y')}, 3m {mv('UST 3m')}",
        f"Global bonds: {b_down} of {len(bonds)} bond ETFs down (= yields up). "
        f"Bund {mv('Germany govt', 2)}, Gilts {mv('UK gilts', 2)}, Canada {mv('Canada govt', 2)}",
        f"FX: DXY {mv('Dollar DXY', 2)}, EUR {mv('EUR/USD', 2)}, USD/JPY {mv('USD/JPY (up=yen weak)', 2)}, "
        f"CNY {mv('USD/CNY', 2)}",
        f"Commods: WTI {mv('WTI crude')}, Gold {mv('Gold')}, Copper {mv('Copper')}, Wheat {mv('Wheat')}, "
        f"Coffee {mv('Coffee')}, Cocoa {mv('Cocoa')}",
    ]
    if sector:
        head.append(f"Sector corr to SPY: {sector[1]:.2f} 10d / {sector[2]:.2f} 21d "
                    f"({sector[3]}th pct, 1y) - high = market moves as one block")
    head.append("Short bounce (below cloud, high in range): " + (", ".join(f"{r['m']} {r['rp']:.2f}" for r in sb) or "none"))
    head.append("Buy dip (above cloud, low in range): " + (", ".join(f"{r['m']} {r['rp']:.2f}" for r in bd) or "none"))

    lines = ["# " + head[0], "", *head[1:], "",
             f"Move = Yahoo (today's bar where open or closed, else last close; * = today's bar). "
             f"Context = MFR at the {mfr_date} close for the ETF in [ ]: cloud = vs MFR Trend cloud, "
             "trade = vs Vola2vola Trade, rp = range position. Bond ETF down = yields up.", ""]
    for sec, _ in BOARD:
        lines.append(f"## {sec}")
        lines.append("```")
        lines.append(f"{'':22}{'last':>10}{'chg':>7}{'1w':>7}{'1m':>7}  {'[mfr]':10}{'cloud':6}{'trade':6}{'rp':>5}  read")
        for r in [x for x in data if x["sec"] == sec]:
            if r.get("last") is None:
                lines.append(f"{r['label'][:21]:22}{'no data':>10}")
                continue
            d = 0 if r["kind"] == "yld" else 1
            last = f"{r['last']:.2f}" if r["last"] < 10000 else f"{r['last']:.0f}"
            star = "*" if r.get("live") else " "
            m = f"[{r['m']}]" if r["m"] else ""
            rp = f"{r['rp']:.2f}" if r["rp"] is not None else ""
            unit = "bp" if r["kind"] == "yld" else ""
            lines.append(f"{(r['label'] + star)[:21]:22}{last:>10}{_n(r['chg'], d) + unit:>7}{_n(r['w'], d):>7}"
                         f"{_n(r['mo'], d):>7}  {m[:10]:10}{r['cloud']:6}{r['trade']:6}{rp:>5}  {r['read']}")
        lines.append("```")
        lines.append("")
    return "\n".join(head), "\n".join(lines)


def save(headline, body):
    out = f"outputs/global_macro/global_macro_{_now():%Y-%m-%d}.md"
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
    except Exception as e:
        log.warning("file save failed: %s", e)
        out = None
    try:
        import db_pg
        import psycopg2
        conn = psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or db_pg._resolve_dsn())
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO global_macro_morning (report_date, headline, body) VALUES (current_date, %s, %s)
                           ON CONFLICT (report_date) DO UPDATE SET headline = EXCLUDED.headline, body = EXCLUDED.body,
                           created_at = now()""", (headline, body))
        conn.close()
    except Exception as e:
        log.warning("db save failed: %s", e)
    return out


def run(send=False, save_it=True):
    data, sector, mfr_date = build()
    headline, body = render(data, sector, mfr_date)
    path = save(headline, body) if save_it else None
    sent = None
    if send:
        import notifier
        sent = notifier.send_telegram("Global macro", "```\n" + headline + "\n```")
        notifier.send_telegram_document(f"global_macro_{_now():%Y-%m-%d}.txt", body,
                                        caption="Global macro morning: full board")
    return {"rows": len(data), "with_data": sum(r.get("chg") is not None for r in data), "path": path,
            "sent": sent, "headline": headline}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    data, sector, mfr_date = build()
    headline, body = render(data, sector, mfr_date)
    print(body)
    if not a.no_save:
        print("saved", save(headline, body))
    if a.send:
        import notifier
        notifier.send_telegram("Global macro", "```\n" + headline + "\n```")
        notifier.send_telegram_document(f"global_macro_{_now():%Y-%m-%d}.txt", body,
                                        caption="Global macro morning: full board")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
