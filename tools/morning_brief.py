"""morning_brief.py — overnight global macro brief, built at 3:30 AM ET.

Writes outputs/briefs/morning_brief_<date>.html (+ LATEST.html) for Kris to
read 4:30–5:50 next to his charts:

  1. Headline (4 lines — HUD-sized)
  2. US index futures (ES / NQ / RTY / YM) vs Hedgeye Risk Ranges
  3. Countries: index · 10y yield · currency · GIP quad (4Q26E→1Q27E)
  4. Commodities: energy, metals, ags vs ranges
  5. Bitcoin
  6. Your book: names near a range edge or a line
  7. If/then playbook (T1A gamma levels + Hedgeye ranges)

    py -m tools.morning_brief            # build + print headline
    py -m tools.morning_brief --open     # also open in the browser

Read-only. Data: Yahoo (overnight prices), hedgeye_risk_ranges, tv_levels,
v_screener, book_positions, t1a_snapshots, data/hedgeye/country_quads_*.csv.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import html
import os
import pathlib
import sys
import warnings

warnings.filterwarnings("ignore")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs" / "briefs"

FUTURES = [  # label, yahoo, hedgeye RR ticker (cash index)
    ("S&P 500 fut", "ES=F", "SPX"), ("Nasdaq 100 fut", "NQ=F", None),
    ("Russell 2000 fut", "RTY=F", "RUT"), ("Dow fut", "YM=F", None), ("VIX", "^VIX", "VIX"),
]
# country: index (yahoo, RR), 10y (tv ticker or None), fx (yahoo, label, RR, invert?), quad name
COUNTRIES = [
    ("US", ("^GSPC", "SPX"), "UST10Y", ("DX-Y.NYB", "Dollar index", "USD", False), "United States"),
    ("Canada", ("^GSPTSE", None), "CA10Y", ("CADUSD=X", "CAD/USD", "CAD/USD", False), "Canada"),
    ("UK", ("^FTSE", None), "GB10Y", ("GBPUSD=X", "GBP/USD", "GBP/USD", False), "United Kingdom"),
    ("Germany", ("^GDAXI", "DAX"), "DE10Y", ("EURUSD=X", "EUR/USD", "EUR/USD", False), "Germany"),
    ("France", ("^FCHI", None), None, None, "France"),
    ("Italy", ("FTSEMIB.MI", None), None, None, "Italy"),
    ("Switzerland", ("^SSMI", None), None, ("CHFUSD=X", "CHF/USD", None, False), "Switzerland"),
    ("Japan", ("^N225", "NIKK"), "JP10Y", ("JPY=X", "USD/JPY", "USD/YEN", True), "Japan"),
    ("China", ("000001.SS", "SSEC"), None, ("CNY=X", "USD/CNY", None, True), "China"),
    ("Hong Kong", ("^HSI", None), None, None, "Hong Kong"),
    ("South Korea", ("^KS11", None), None, ("KRW=X", "USD/KRW", None, True), "South Korea"),
    ("Taiwan", ("^TWII", None), None, ("TWD=X", "USD/TWD", None, True), "Taiwan"),
    ("India", ("^NSEI", None), None, ("INR=X", "USD/INR", None, True), "India"),
    ("Australia", ("^AXJO", None), None, ("AUDUSD=X", "AUD/USD", None, False), "Australia"),
    ("Brazil", ("^BVSP", None), None, ("BRL=X", "USD/BRL", None, True), "Brazil"),
    ("Mexico", ("^MXX", None), None, ("MXN=X", "USD/MXN", None, True), "Mexico"),
    # — rest of Hedgeye's 47-country GIP table —
    ("Eurozone", ("^STOXX50E", None), None, ("EURUSD=X", "EUR/USD", "EUR/USD", False), "Eurozone"),
    ("Emerging Mkts", ("EEM", None), None, None, "Emerging Markets"),
    ("Spain", ("^IBEX", None), None, None, "Spain"),
    ("Netherlands", ("^AEX", None), None, None, "Netherlands"),
    ("Belgium", ("^BFX", None), None, None, "Belgium"),
    ("Austria", ("^ATX", None), None, None, "Austria"),
    ("Ireland", ("^ISEQ", None), None, None, "Ireland"),
    ("Finland", ("^OMXH25", None), None, None, "Finland"),
    ("Portugal", ("PSI20.LS", None), None, None, "Portugal"),
    ("Greece", ("GD.AT", None), None, None, "Greece"),
    ("Sweden", ("^OMX", None), None, ("SEK=X", "USD/SEK", None, True), "Sweden"),
    ("Norway", ("OBX.OL", None), None, ("NOK=X", "USD/NOK", None, True), "Norway"),
    ("Denmark", ("^OMXC25", None), None, ("DKK=X", "USD/DKK", None, True), "Denmark"),
    ("Poland", ("WIG20.WA", None), None, ("PLN=X", "USD/PLN", None, True), "Poland"),
    ("Czech Rep.", ("^PX", None), None, ("CZK=X", "USD/CZK", None, True), "Czech Republic"),
    ("Russia", ("IMOEX.ME", None), None, ("RUB=X", "USD/RUB", None, True), "Russia"),
    ("Turkey", ("XU100.IS", None), None, ("TRY=X", "USD/TRY", None, True), "Turkey"),
    ("Israel", ("^TA125.TA", None), None, ("ILS=X", "USD/ILS", None, True), "Israel"),
    ("Saudi Arabia", ("^TASI.SR", None), None, ("SAR=X", "USD/SAR", None, True), "Saudi Arabia"),
    ("South Africa", ("^J203.JO", None), None, ("ZAR=X", "USD/ZAR", None, True), "South Africa"),
    ("Singapore", ("^STI", None), None, ("SGD=X", "USD/SGD", None, True), "Singapore"),
    ("Malaysia", ("^KLSE", None), None, ("MYR=X", "USD/MYR", None, True), "Malaysia"),
    ("Indonesia", ("^JKSE", None), None, ("IDR=X", "USD/IDR", None, True), "Indonesia"),
    ("Thailand", ("^SET.BK", None), None, ("THB=X", "USD/THB", None, True), "Thailand"),
    ("Philippines", ("PSEI.PS", None), None, ("PHP=X", "USD/PHP", None, True), "Philippines"),
    ("New Zealand", ("^NZ50", None), None, ("NZDUSD=X", "NZD/USD", None, False), "New Zealand"),
    ("Chile", ("^IPSA", None), None, ("CLP=X", "USD/CLP", None, True), "Chile"),
    ("Colombia", ("^COLCAP", None), None, ("COP=X", "USD/COP", None, True), "Colombia"),
    ("Peru", ("^SPBLPGPT", None), None, ("PEN=X", "USD/PEN", None, True), "Peru"),
    ("Argentina", ("^MERV", None), None, ("ARS=X", "USD/ARS", None, True), "Argentina"),
]
# tradeable proxies: country ETF (trend/range from v_screener) and currency ETF
COUNTRY_ETF = {"US": "SPY", "Canada": "EWC", "UK": "EWU", "Germany": "EWG", "France": "EWQ",
               "Italy": "EWI", "Switzerland": "EWL", "Japan": "EWJ", "China": "FXI",
               "Hong Kong": "EWH", "South Korea": "EWY", "Taiwan": "EWT", "India": "INDA",
               "Australia": "EWA", "Brazil": "EWZ", "Mexico": "EWW",
               "Eurozone": "EZU", "Emerging Mkts": "EEM", "Spain": "EWP", "Netherlands": "EWN",
               "Belgium": "EWK", "Austria": "EWO", "Ireland": "EIRL", "Finland": "EFNL",
               "Portugal": "PGAL", "Greece": "GREK", "Sweden": "EWD", "Norway": "NORW",
               "Denmark": "EDEN", "Poland": "EPOL", "Czech Rep.": None, "Russia": None,
               "Turkey": "TUR", "Israel": "EIS", "Saudi Arabia": "KSA", "South Africa": "EZA",
               "Singapore": "EWS", "Malaysia": "EWM", "Indonesia": "EIDO", "Thailand": "THD",
               "Philippines": "EPHE", "New Zealand": "ENZL", "Chile": "ECH", "Colombia": "COLO",
               "Peru": "EPU", "Argentina": "ARGT"}
# US-listed currency ETFs (no FX account needed). Each measures the LOCAL currency
# vs USD (UUP = long the dollar). No live US-listed ETF for CNY/KRW/TWD/INR/BRL/MXN
# — CEW (emerging-market currency basket) is the proxy for those.
FX_ETF = {"US": "UUP", "Canada": "FXC", "UK": "FXB", "Germany": "FXE", "France": "FXE",
          "Italy": "FXE", "Switzerland": "FXF", "Japan": "FXY", "Australia": "FXA",
          **{c: "FXE" for c in ("Eurozone", "Spain", "Netherlands", "Belgium", "Austria", "Ireland",
                                "Finland", "Portugal", "Greece")}}
EM_FX_PROXY = "CEW"
INDEX_NAME = {"US": "S&P 500", "Canada": "S&P/TSX", "UK": "FTSE 100", "Germany": "DAX", "France": "CAC 40",
    "Italy": "FTSE MIB", "Switzerland": "SMI", "Japan": "Nikkei 225", "China": "Shanghai Comp",
    "Hong Kong": "Hang Seng", "South Korea": "KOSPI", "Taiwan": "TAIEX", "India": "Nifty 50",
    "Australia": "ASX 200", "Brazil": "Bovespa", "Mexico": "IPC", "Eurozone": "Euro Stoxx 50",
    "Emerging Mkts": "MSCI EM", "Spain": "IBEX 35", "Netherlands": "AEX", "Belgium": "BEL 20",
    "Austria": "ATX", "Ireland": "ISEQ", "Finland": "OMXH 25", "Portugal": "PSI", "Greece": "Athens Gen",
    "Sweden": "OMXS 30", "Norway": "OBX", "Denmark": "OMXC 25", "Poland": "WIG20", "Czech Rep.": "PX",
    "Russia": "MOEX", "Turkey": "BIST 100", "Israel": "TA-125", "Saudi Arabia": "Tadawul",
    "South Africa": "JSE Top 40", "Singapore": "STI", "Malaysia": "KLCI", "Indonesia": "Jakarta Comp",
    "Thailand": "SET", "Philippines": "PSEi", "New Zealand": "NZX 50", "Chile": "IPSA",
    "Colombia": "COLCAP", "Peru": "S&P/BVL", "Argentina": "Merval"}
# inverse ETFs you hold long: judge them on the UNDERLYING as a short
INVERSE = {"METD": "META", "AMZD": "AMZN", "MSFD": "MSFT", "GGLS": "GOOGL", "PSQ": "QQQ",
           "SQQQ": "QQQ", "SH": "SPY", "RWM": "IWM", "DOG": "DIA", "SBIT": "IBIT", "SETH": "ETHA"}
QUAD_NAME = {"1": "Goldilocks", "2": "Reflation", "3": "Stagflation", "4": "Deflation"}


def trade_trend(px, va, vb, mlo, mup, invert=False):
    """TRADE = price vs the Vola2vola lines (above both = Bull, below both = Bear);
    TREND = price vs the MFR Trend band midpoint (Keith's TREND ~ band middle)."""
    tr = td = None
    if px is not None and va is not None and vb is not None:
        hi, lo = max(va, vb), min(va, vb)
        tr = "BULLISH" if px > hi else "BEARISH" if px < lo else "NEUTRAL"
    if px is not None and mlo is not None and mup is not None:
        td = "BULLISH" if px >= (mlo + mup) / 2 else "BEARISH"
    if invert:
        flip = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}
        tr, td = flip.get(tr, tr), flip.get(td, td)
    return tr, td


def levels(px, va, vb, mlo, mup):
    """(price, TRADE line, TREND line): TRADE = upper Vola2vola line, TREND = MFR band middle."""
    tl = max(va, vb) if va is not None and vb is not None else None
    tn = (mlo + mup) / 2 if mlo is not None and mup is not None else None
    return (px, tl, tn)


def action(trend, rp, etf):
    """'BUY SPY' style call from trend + range position."""
    if not etf:
        return None
    t = (trend or "").upper()
    if t == "BULLISH":
        return f"BUY {etf}" + (" (dip)" if rp is not None and rp <= 0.40 else " on pullback")
    if t == "BEARISH":
        return f"SHORT {etf} (bounce)" if rp is not None and rp >= 0.60 else f"SELL/AVOID {etf}"
    if t == "NEUTRAL":
        return f"WAIT {etf}"
    return None


TV_INDEX = {"US": "SPX", "Germany": "DAX", "Japan": "NIKK", "China": "SSEC"}
TV_FX = {"Germany": ("EUR/USD", False), "Eurozone": ("EUR/USD", False), "UK": ("GBP/USD", False),
         "Japan": ("USD/YEN", True), "Canada": ("CAD/USD", False), "China": ("USD/CNY", True),
         "US": ("USD", False)}


def recommend(trend, rp, quad):
    """Process verdict for a country ETF: trend + range position, quad as context.
    Signals win when the quad disagrees."""
    t = (trend or "").upper()
    good_q = quad in ("1", "2")
    if t == "BULLISH":
        v = "BUY (dip)" if rp is not None and rp <= 0.40 else "LONG · buy pullbacks"
        return v + ("" if good_q or not quad else " · quad says caution")
    if t == "BEARISH":
        v = "SHORT (bounce)" if rp is not None and rp >= 0.60 else "AVOID · short next bounce"
        return v + ("" if not good_q or not quad else " · quad disagrees")
    if t == "NEUTRAL":
        return "WAIT · no trend"
    return "—"


EURO_BONDS = [("Bund fut (px)", "FGBL_F"), ("OAT fut (px)", "FOAT_F"), ("BTP fut (px)", "FBTP_F")]
COMMODITIES = [  # label, yahoo, RR
    ("Brent", "BZ=F", "BRENT"), ("WTI", "CL=F", "WTIC"), ("Nat gas", "NG=F", "NATGAS"),
    ("Heating oil", "HO=F", None), ("Gasoline", "RB=F", None),
    ("Gold", "GC=F", "GOLD"), ("Silver", "SI=F", "SILVER"), ("Copper", "HG=F", "COPPER"),
    ("Platinum", "PL=F", None), ("Palladium", "PA=F", None),
    ("Corn", "ZC=F", None), ("Wheat", "ZW=F", None), ("Soybeans", "ZS=F", None),
]
ROLL_WARN = 4.0   # % — a futures move bigger than this gets a "check contract roll" flag


# ── data ──────────────────────────────────────────────────────────────────
def yahoo(symbols):
    """{sym: (last, prev_close, pct, asof)} — prev = the prior daily close."""
    import yfinance as yf
    out = {}
    d = yf.download(sorted(set(symbols)), period="7d", interval="1d", progress=False,
                    auto_adjust=False, group_by="ticker", threads=True)
    for s in set(symbols):
        try:
            c = d[s]["Close"].dropna()
            if len(c) < 2:
                continue
            out[s] = (float(c.iloc[-1]), float(c.iloc[-2]),
                      100 * (c.iloc[-1] / c.iloc[-2] - 1), c.index[-1].date())
        except Exception:
            pass
    # Futures: the "=F" daily history splices contracts at a roll, so iloc[-2]
    # can be the OLD contract (10/22 fake "Brent -3%"). The quote's
    # previousClose is the current contract's prior settle — use that.
    for s in [x for x in set(symbols) if x.endswith("=F")]:
        try:
            info = yf.Ticker(s).info
            last, prev = info.get("regularMarketPrice"), info.get("regularMarketPreviousClose") or info.get("previousClose")
            if last and prev:
                asof = out.get(s, (None, None, None, dt.date.today()))[3]
                out[s] = (float(last), float(prev), 100 * (last / prev - 1), asof)
        except Exception:
            pass
    return out


def db():
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""SELECT ticker, trend, buy_trade, sell_trade FROM hedgeye_risk_ranges
                       WHERE signal_date = (SELECT max(signal_date) FROM hedgeye_risk_ranges)""")
        rr = {t: (tr, float(b), float(s)) for t, tr, b, s in cur.fetchall()}
        cur.execute("SELECT max(signal_date) FROM hedgeye_risk_ranges")
        rr_date = cur.fetchone()[0]
        cur.execute("""SELECT DISTINCT ON (ticker) ticker, bar_date, bar_close, mfr_trend_lower,
                              mfr_trend_upper, range_low, range_high, vola_a, vola_b
                       FROM tv_levels ORDER BY ticker, bar_date DESC""")
        tv = {r[0]: r[1:7] for r in cur.fetchall()} if False else None
        cur.execute("""SELECT DISTINCT ON (ticker) ticker, bar_date, bar_close, mfr_trend_lower,
                              mfr_trend_upper, range_low, range_high, vola_a, vola_b
                       FROM tv_levels ORDER BY ticker, bar_date DESC""")
        tvx = {r[0]: r[1:] for r in cur.fetchall()}
        tv = {k: v[:6] for k, v in tvx.items()}
        cur.execute("""SELECT b.symbol, sum(b.market_value), v.price, v.range_low, v.range_high,
                              v.tv_trade, v.tv_mfr_upper, v.tv_mfr_lower, v.trend_dir
                       FROM book_positions b LEFT JOIN v_screener v ON v.ticker = b.symbol
                       WHERE b.snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
                         AND b.asset_class <> 'cash' AND b.symbol NOT IN ('BUXX','CLOX','SKYDWS')
                       GROUP BY 1,3,4,5,6,7,8,9""")
        book = cur.fetchall()
        cur.execute("""SELECT report_date, gamma_exposure, systematic_flow_risk, gex_flip,
                              lower_pv_band, upper_pv_band, resistance_strike, focal_strike,
                              support_strike, spx_last
                       FROM t1a_snapshots ORDER BY report_date DESC LIMIT 1""")
        t1a = cur.fetchone()
        cur.execute("SELECT ticker, trend_dir, range_pos, price, range_low, range_high, tv_vola_a, tv_vola_b, tv_mfr_lower, tv_mfr_upper FROM v_screener WHERE ticker = ANY(%s)",
                    (list(COUNTRY_ETF.values()) + list(FX_ETF.values()) + [EM_FX_PROXY],))
        f = lambda x: float(x) if x is not None else None
        scr, scr_lines = {}, {}
        for t, td, rp, p, lo, hi, va, vb, ml, mu in cur.fetchall():
            scr[t] = (td, f(rp), f(p), f(lo), f(hi))
            scr_lines[t] = (f(p), f(va), f(vb), f(ml), f(mu))
        cur.execute("""SELECT ticker, prev_close FROM hedgeye_risk_ranges
                       WHERE signal_date = (SELECT max(signal_date) FROM hedgeye_risk_ranges)""")
        rr_prev = {t: float(p) for t, p in cur.fetchall() if p is not None}
        cur.execute("""
            SELECT b.symbol,
                   string_agg(DISTINCT CASE b.account_name WHEN 'Individual' THEN 'Ind'
                                        WHEN 'Rollover IRA' THEN 'IRA' WHEN 'ROTH IRA' THEN 'Roth'
                                        ELSE b.account_name END, '/') AS accts,
                   sum(b.market_value), sum(b.total_gl_dollar), sum(b.cost_basis),
                   v.price, v.range_low, v.range_high, v.tv_vola_a, v.tv_vola_b,
                   v.tv_mfr_lower, v.tv_mfr_upper, v.trend_dir,
                   coalesce(t.hedgeye_group, v.hedgeye_group), coalesce(t.hedgeye_bucket_0629, v.hedgeye_bucket_0629),
                   (SELECT side FROM ss_roster_history s WHERE s.ticker = b.symbol AND s.removed_on IS NULL LIMIT 1)
            FROM book_positions b
            LEFT JOIN v_screener v ON v.ticker = b.symbol
            LEFT JOIN ticker_tags t ON t.ticker = b.symbol
            WHERE b.snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
              AND b.asset_class <> 'cash' AND b.symbol NOT IN ('SKYDWS')
            GROUP BY b.symbol, v.price, v.range_low, v.range_high, v.tv_vola_a, v.tv_vola_b,
                     v.tv_mfr_lower, v.tv_mfr_upper, v.trend_dir, 14, 15""")
        book_full = cur.fetchall()
        cur.execute("""SELECT v.ticker, v.price, v.range_low, v.range_high, v.tv_vola_a, v.tv_vola_b,
                              v.tv_mfr_lower, v.tv_mfr_upper, v.trend_dir,
                              coalesce(t.hedgeye_group, v.hedgeye_group), coalesce(t.hedgeye_bucket_0629, v.hedgeye_bucket_0629),
                              (SELECT side FROM ss_roster_history s WHERE s.ticker = v.ticker AND s.removed_on IS NULL LIMIT 1)
                       FROM v_screener v LEFT JOIN ticker_tags t ON t.ticker = v.ticker
                       WHERE v.ticker = ANY(%s)""", (list(set(INVERSE.values())),))
        under = {r[0]: r[1:] for r in cur.fetchall()}
        cur.execute("""SELECT account_name,
                              sum(CASE WHEN market_value > 0 AND asset_class <> 'cash' AND symbol NOT IN ('BUXX','CLOX') THEN market_value ELSE 0 END),
                              sum(CASE WHEN market_value < 0 THEN market_value ELSE 0 END),
                              sum(CASE WHEN asset_class = 'cash' OR symbol IN ('BUXX','CLOX') THEN market_value ELSE 0 END),
                              max(snapshot_date)
                       FROM book_positions WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
                       GROUP BY 1 ORDER BY 1""")
        acct_tot = cur.fetchall()
    return rr, rr_date, tv, book, t1a, scr, rr_prev, tvx, scr_lines, book_full, acct_tot, under


def country_quads():
    files = sorted(glob.glob(str(ROOT / "data" / "hedgeye" / "country_quads_*.csv")))
    if not files:
        return {}, None
    with open(files[-1], encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    cols = list(rows[0].keys())
    cur_q = next((c for c in cols if c.endswith("E") and "E/A" not in c), None)
    nxt = cols[cols.index(cur_q) + 1] if cur_q and cols.index(cur_q) + 1 < len(cols) else None
    return {r["Country"]: (r.get(cur_q), r.get(nxt)) for r in rows}, (cur_q, nxt)


# ── formatting helpers ────────────────────────────────────────────────────
def rpos(px, lo, hi):
    return (px - lo) / (hi - lo) if px is not None and lo is not None and hi and hi > lo else None


def pct(v, roll=False):
    if v is None:
        return "<td>—</td>"
    cls = "up" if v > 0 else "dn" if v < 0 else ""
    flag = " ⚠" if roll and abs(v) > ROLL_WARN else ""
    return f'<td class="{cls}">{v:+.2f}%{flag}</td>'


def num(v, d=2):
    return "—" if v is None else (f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.{d}f}")


def rng_cell(rp):
    if rp is None:
        return "<td>—</td>"
    tag = " top" if rp >= 0.85 else " bot" if rp <= 0.15 else ""
    w = max(0, min(100, rp * 100))
    return (f'<td class="rng{tag}"><span class="bar"><i style="left:{w:.0f}%"></i></span>'
            f' {rp:.2f}</td>')


def trend_cell(t):
    t = (t or "").upper()
    cls = {"BULLISH": "up", "BEARISH": "dn"}.get(t, "")
    return f'<td class="{cls}">{t.title() or "—"}</td>'


# ── build ─────────────────────────────────────────────────────────────────
def build():
    rr, rr_date, tv, book, t1a, scr, rr_prev, tvx, scr_lines, book_full, acct_tot, under = db()
    quads, qcols = country_quads()
    syms = [f[1] for f in FUTURES] + [c[1][0] for c in COUNTRIES] + \
           [c[3][0] for c in COUNTRIES if c[3]] + [c[1] for c in COMMODITIES] + ["BTC-USD"]
    y = yahoo(syms)
    now = dt.datetime.now()
    notes = []          # (priority, text) for the headline

    def rr_rp(key, px):
        if key and key in rr and px is not None:
            t, b, s = rr[key]
            return t, b, s, rpos(px, b, s)
        return None, None, None, None

    # 1) futures
    fut_rows = []
    for label, sym, key in FUTURES:
        last, prev, p, asof = y.get(sym, (None, None, None, None))
        t, b, s, rp = rr_rp(key, last)
        fut_rows.append((label, last, p, t, b, s, rp))
        if label.startswith("S&P") and p is not None:
            notes.append((0, f"S&P fut {p:+.1f}% · " + (f"range {b:,.0f}–{s:,.0f}" if b else "")))
        if label == "VIX" and last:
            notes.append((3, f"VIX {last:.1f}" + (f" (top {s:.1f})" if s else "")))

    # 2) countries — every leg gets a range (Hedgeye RR first, then TradingView/ETF)
    c_rows = []
    for name, (isym, ikey), ytk, fx, qname in COUNTRIES:
        il, ip, ipct, _ = y.get(isym, (None, None, None, None))
        it, ib, is_, irp = rr_rp(ikey, il)
        etf = COUNTRY_ETF.get(name)
        et, erp, _, elo, ehi = scr.get(etf, (None, None, None, None, None))
        idx = dict(pct=ipct, trend=it or et, rp=irp if irp is not None else erp,
                   rng=(f"{ib:,.0f}–{is_:,.0f}" if ib else (f"{etf} {elo:.2f}–{ehi:.2f}" if elo else "")))
        # 10y yield: Hedgeye RR (US) else TradingView range
        yd = dict(last=None, trend=None, rp=None, rng="")
        if ytk and ytk in rr:
            t, b, s_ = rr[ytk]
            yd.update(last=rr_prev.get(ytk), trend=t, rp=rpos(rr_prev.get(ytk), b, s_), rng=f"{b:.2f}–{s_:.2f}")
        elif ytk and ytk in tv:
            _, close, mlo, mup, rlo, rhi = tv[ytk]
            if close is not None:
                v = float(close)
                yd.update(last=v, trend=("BULLISH" if mup and v > float(mup) else "BEARISH" if mlo and v < float(mlo) else "NEUTRAL"),
                          rp=rpos(v, float(rlo), float(rhi)) if rlo and rhi else None,
                          rng=f"{float(rlo):.2f}–{float(rhi):.2f}" if rlo and rhi else "")
        # currency: Hedgeye RR else currency-ETF range
        fd = dict(label="", pct=None, trend=None, rp=None, rng="")
        if fx:
            fsym, flabel, fkey, inv = fx
            fl, _, fp, _ = y.get(fsym, (None, None, None, None))
            ft, fb, fs, frp = rr_rp(fkey, fl)
            frng = f"{fb:.4g}–{fs:.4g}" if fb else ""
            if not ft and FX_ETF.get(name) in scr:
                fe = FX_ETF[name]
                ft, frp, _, flo, fhi = scr[fe]
                frng = f"{fe} {flo:.2f}–{fhi:.2f}" if flo else ""
                inv_trend = False          # ETF already measures the local currency
            else:
                inv_trend = inv
            if inv:
                flabel = flabel.split("/")[1]
                fp = -fp if fp is not None else None
            if inv_trend:
                ft = {"BULLISH": "BEARISH", "BEARISH": "BULLISH"}.get(ft or "", ft)
                frp = (1 - frp) if frp is not None else None
            fd.update(label=flabel, pct=fp, trend=ft, rp=frp, rng=frng)
        fetf = FX_ETF.get(name) or (EM_FX_PROXY if name not in ("Hong Kong", "Saudi Arabia", "Denmark") else None)
        fd["etf"] = fetf
        if name == "US":
            fd["call"] = recommend(fd["trend"], fd["rp"], None).replace("SHORT", "SELL") if fd["trend"] else "—"
        elif FX_ETF.get(name):
            fd["call"] = recommend(fd["trend"], fd["rp"], None).replace("SHORT (bounce)", "SELL/AVOID (bounce)") if fd["trend"] else "—"
        else:
            fd["call"] = "no single-ccy ETF · CEW = EM basket"
        # trade/trend on each leg
        def tvtt(key, invert=False):
            r_ = tvx.get(key)
            if not r_:
                return None, None
            _, close, mlo, mup, _, _, va, vb = r_
            g = lambda x: float(x) if x is not None else None
            return trade_trend(g(close), g(va), g(vb), g(mlo), g(mup), invert)

        def tvlv(key, invert=False):
            r_ = tvx.get(key)
            if not r_:
                return None
            _, close, mlo, mup, _, _, va, vb = r_
            g = lambda x: float(x) if x is not None else None
            return levels(g(close), g(va), g(vb), g(mlo), g(mup)) + (invert,)
        idx["tt"] = tvtt(TV_INDEX.get(name)) if TV_INDEX.get(name) else (None, None)
        if ikey and ikey in rr:
            idx["tt"] = (idx["tt"][0], rr[ikey][0])          # Hedgeye's own TREND wins
        el = scr_lines.get(etf)
        idx["etf_tt"] = trade_trend(*el) if el else (None, None)
        idx["etf_rp"] = erp
        yd["tt"] = tvtt(ytk) if ytk else (None, None)
        if ytk in rr:
            yd["tt"] = (yd["tt"][0], rr[ytk][0])
        fk = TV_FX.get(name)
        fd["tt"] = tvtt(*fk) if fk else (None, None)
        if fd.get("trend") and (fd["tt"][1] is None or (fx and fx[2] in rr)):
            fd["tt"] = (fd["tt"][0], fd["trend"])   # Hedgeye's published TREND wins
        idx["lv"] = tvlv(TV_INDEX.get(name)) if TV_INDEX.get(name) else None
        idx["etf_lv"] = (levels(*el) + (False,)) if el else None
        yd["lv"] = tvlv(ytk) if ytk else None
        fd["lv"] = tvlv(*fk) if fk else None
        if fx and fx[3] and fd.get("label"):
            fd["label"] = f"{fd['label']} (USD/{fd['label']})"
        q = quads.get(qname, (None, None))
        eq_trend = idx["etf_tt"][1] or idx["trend"]
        eq_act = action(eq_trend, erp if erp is not None else idx["rp"], etf)
        fxe = "UUP" if name == "US" else FX_ETF.get(name)
        fx_act = action(fd.get("tt", (None, None))[1], fd.get("rp"), fxe) if fxe else None
        if fx_act and fx_act.startswith("WAIT"):
            fx_act = None
        t_eq = (eq_trend or "").upper()
        q_note = ""
        if q[0] and ((q[0] in "12" and t_eq == "BEARISH") or (q[0] in "34" and t_eq == "BULLISH")):
            q_note = " · quad disagrees (signal wins)"
        rec = (" · ".join(x for x in (eq_act, fx_act) if x) or "—") + q_note
        c_rows.append(dict(name=name, idx=idx, yld=yd, fx=fd, q=q, etf=etf, rec=rec))
    short = {"South Korea": "Korea", "Hong Kong": "HK"}
    asia = [r for r in c_rows if r["name"] in ("Japan", "China", "South Korea", "Hong Kong", "Taiwan", "India")]
    eu = [r for r in c_rows if r["name"] in ("Germany", "UK", "France", "Italy")]
    line_asia = "Asia: " + " · ".join(f"{short.get(r['name'], r['name'])} {r['idx']['pct']:+.1f}" for r in asia if r["idx"]["pct"] is not None)
    line_eu = "Europe: " + " · ".join(f"{r['name']} {r['idx']['pct']:+.1f}" for r in eu if r["idx"]["pct"] is not None)

    # 3) commodities
    com_rows = []
    for label, sym, key in COMMODITIES:
        last, prev, p, asof = y.get(sym, (None, None, None, None))
        t, b, s, rp = rr_rp(key, last)
        com_rows.append((label, last, p, t, b, s, rp))
    br = next((r for r in com_rows if r[0] == "Brent"), None)
    gd = next((r for r in com_rows if r[0] == "Gold"), None)
    if br and br[2] is not None:
        notes.append((1, f"Brent {br[1]:.1f} {br[2]:+.1f}%" + (f" · gold {gd[2]:+.1f}%" if gd and gd[2] is not None else "")))

    btc = y.get("BTC-USD")
    bt, bb, bs, brp = rr_rp("BITCOIN", btc[0] if btc else None)
    if brp is not None and (brp < 0 or brp > 1):
        notes.append((1.5, f"BTC {btc[0]:,.0f} {'BELOW' if brp < 0 else 'ABOVE'} range {bb:,.0f}–{bs:,.0f}"))

    # 4) book watch
    watch = {"stop": [], "trim": [], "add": [], "cover": []}
    for sym, mv, px, lo, hi, tr, mu, ml, td in book:
        if px is None or lo is None:
            continue
        px, mv = float(px), float(mv)
        rp = rpos(px, float(lo), float(hi))
        mid = (float(mu) + float(ml)) / 2 if mu and ml else None
        short = mv < 0
        if mid and ((not short and px < mid) or (short and px > mid)):
            watch["stop"].append(f"{sym} {px:.2f} {'>' if short else '<'} trend {mid:.2f}")
        elif rp is not None and not short and rp >= 0.85:
            watch["trim"].append(f"{sym} {rp:.2f}")
        elif rp is not None and not short and rp <= 0.30 and td == "BULLISH":
            watch["add"].append(f"{sym} {rp:.2f}")
        elif rp is not None and short and rp <= 0.20:
            watch["cover"].append(f"{sym} {rp:.2f}")

    # 4b) full book brief
    book_rows = []
    f = lambda x: float(x) if x is not None else None
    for (sym, accts, mv, gl, cost, px, lo, hi, va, vb, ml, mu, td, grp, bucket, ss) in book_full:
        mv, gl, cost, px, lo, hi, va, vb, ml, mu = map(f, (mv, gl, cost, px, lo, hi, va, vb, ml, mu))
        if mv is None or abs(mv) < 1:
            continue
        short = mv < 0
        inv_of = INVERSE.get(sym)
        if inv_of and inv_of in under:          # judge the inverse ETF as a SHORT of its underlying
            u = under[inv_of]
            px, lo, hi, va, vb, ml, mu = map(f, u[:7])
            td, grp, bucket, ss = u[7], u[8], u[9], u[10]
            short = True
        rp = rpos(px, lo, hi)
        _, tl, tn = levels(px, va, vb, ml, mu)
        lists = []
        if ss:
            lists.append(f"SS {ss}")
        if bucket:
            lists.append(bucket.replace("_", " "))
        cash_like = sym in ("BUXX", "CLOX", "ILS")
        if cash_like:
            act = "HOLD (cash-like)"
        elif px is None:
            act = "—"
        elif not short:
            if tn and px < tn:
                act = "STOP · below trend"
            elif rp is not None and rp >= 0.85:
                act = "TRIM · top of range"
            elif rp is not None and rp <= 0.30 and (td or "").upper() == "BULLISH":
                act = "ADD · bottom of range"
            elif tl and px < tl:
                act = "WATCH · below trade"
            else:
                act = "HOLD"
            pm_short = (bucket or "").endswith("short")
            if ss == "long" and pm_short:
                act += " · Position Monitor says SHORT, Keith's Signal Strength says LONG"
            elif ss == "short":
                act += " · Keith's Signal Strength says SHORT"
            elif pm_short:
                act += " · Position Monitor says SHORT"
        else:
            if tn and px > tn:
                act = "COVER · above trend"
            elif rp is not None and rp <= 0.20:
                act = "COVER SOME · low end"
            elif rp is not None and rp >= 0.70:
                act = "ADD SHORT · top of range"
            else:
                act = "HOLD SHORT"
            if ss == "long":
                act += " · Keith's Signal Strength says LONG"
            elif (bucket or "").endswith("long"):
                act += " · Position Monitor says LONG"
        pnl = (gl / cost * 100) if gl is not None and cost else None
        if inv_of:
            act = act.replace("HOLD SHORT", "HOLD") + f" · (inverse ETF = short {inv_of}; levels shown are {inv_of}'s)"
            if ss == "short":
                act = act.replace(f" · (inverse", f" · Keith's Signal Strength says SHORT {inv_of} ✓ · (inverse")
        book_rows.append(dict(sym=sym if not inv_of else f"{sym} → short {inv_of}", accts=accts, mv=mv, pnl=pnl,
                              px=px, rp=rp, tl=tl, tn=tn, short=short, grp=(grp or "").title(),
                              lists=", ".join(lists), act=act))
    order = {"STOP": 0, "COVER ·": 0, "TRIM": 1, "COVER SOME": 1, "ADD": 2, "WATCH": 3, "HOLD": 4}
    book_rows.sort(key=lambda r: (r["short"], min([v for k, v in order.items() if r["act"].startswith(k)] or [5]), -abs(r["mv"])))

    # 5) if/then
    ifthen = []
    if t1a:
        _, gex, flow, flip, lo_b, up_b, res, foc, sup, spx_last = t1a
        if sup:
            ifthen.append((f"SPX loses {float(sup):,.0f} (put wall)" + (f" / {float(lo_b):,.0f} band low" if lo_b else ""),
                           "No new longs; cover ⅓ of shorts at the range low"))
        if flip:
            ifthen.append((f"SPX back above {float(flip):,.0f} (gamma flip)", "Vol calms: OK to add longs on red"))
        if res:
            ifthen.append((f"SPX rallies into {float(res):,.0f} (call wall)", "Trim longs / add shorts into strength"))
    if "UST10Y" in rr:
        _, b, s = rr["UST10Y"]
        ifthen.append((f"10Y below {b:.2f} (range low) → 4.85 trend", "Bonds bid: TLT on pullback; cut housing shorts"))
    if "BRENT" in rr:
        _, b, s = rr["BRENT"]
        ifthen.append((f"Brent > {s:.0f} (range top)", "Trim producers into it"))
        ifthen.append((f"Brent < {b:.0f} (range low)", "Energy hedge breaking: cut producers to minimum"))
    if "VIX" in rr:
        ifthen.append((f"VIX > {rr['VIX'][2]:.1f} (range top)", "Chop zone: halve position sizes"))

    notes.sort(key=lambda x: x[0])
    fut = next((n[1] for n in notes if n[1].startswith("S&P")), "")
    vix = next((n[1] for n in notes if n[1].startswith("VIX")), "")
    com = next((n[1] for n in notes if n[1].startswith("Brent")), "")
    alerts = [n[1] for n in notes if n[1].startswith("BTC")]
    headline = [x for x in (f"{fut} · {vix}".strip(" ·"), com, line_asia, line_eu) if x] + alerts
    return dict(now=now, rr_date=rr_date, fut=fut_rows, countries=c_rows, qcols=qcols,
                euro_bonds=[(l, tv.get(t)) for l, t in EURO_BONDS], com=com_rows,
                btc=(btc, bt, bb, bs, brp), watch=watch, ifthen=ifthen, headline=headline,
                book_rows=book_rows, acct_tot=acct_tot,
                t1a=t1a)


CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--up:#0a8f3c;--dn:#c62828;--card:#f6f6f8}
@media (prefers-color-scheme:dark){:root{--bg:#111214;--fg:#eceef1;--mut:#9aa0a6;--line:#2a2c30;--up:#3ddc84;--dn:#ff6b6b;--card:#1a1b1e}}
body{background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:16px;max-width:1100px}
h1{font-size:20px;margin:0 0 2px}h2{font-size:15px;margin:22px 0 6px;border-bottom:1px solid var(--line);padding-bottom:4px}
.mut{color:var(--mut);font-size:12px}.hud{background:var(--card);border-radius:10px;padding:10px 14px;font:600 15px/1.6 ui-monospace,Consolas,monospace;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--mut);font-weight:600}td:first-child,th:first-child{text-align:left}.up{color:var(--up)}.dn{color:var(--dn)}
.bar{display:inline-block;width:56px;height:6px;background:var(--line);border-radius:3px;position:relative;vertical-align:middle}
.bar i{position:absolute;top:-3px;width:3px;height:12px;background:var(--fg);border-radius:1px;margin-left:-1px}
.rng.top{color:var(--dn);font-weight:600}.warn{color:#c77700}.rng.bot{color:var(--up);font-weight:600}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:700px){.grid{grid-template-columns:1fr}.wrap{overflow-x:auto}}
.chip{background:var(--card);border-radius:8px;padding:8px 10px}.chip b{display:block;font-size:12px;color:var(--mut);margin-bottom:2px}
"""


def render(b):
    e = html.escape
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Morning Brief</title><style>{CSS}</style></head><body>",
           f"<h1>Morning Brief · {b['now']:%a %b %d}</h1>",
           f"<div class='mut'>Built {b['now']:%I:%M %p} · Hedgeye ranges from {b['rr_date']} · "
           f"range bar: left = bottom (0), right = top (1); red ≥0.85, green ≤0.15 · ⚠ = check futures roll</div>",
           "<div class='hud'>" + "<br>".join(e(h) for h in b["headline"]) + "</div>"]

    def tbl(head, rows):
        return ("<div class='wrap'><table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>" +
                "".join("<tr>" + r + "</tr>" for r in rows) + "</table></div>")

    out.append("<h2>US index futures</h2>")
    out.append(tbl(["", "Last", "Overnight", "Trend", "Range (Hedgeye)", "Position"],
                   [f"<td>{l}</td><td>{num(x)}</td>{pct(p, True)}{trend_cell(t)}"
                    f"<td>{num(lo)} – {num(hi)}</td>{rng_cell(rp)}" for l, x, p, t, lo, hi, rp in b["fut"]]))

    qc = b["qcols"] or ("", "")
    out.append("<h2>Countries (all 47 in Hedgeye's GIP model) · index · 10y yield · currency · quad</h2>")
    rows = []
    def lv_cells(lv):
        """TRADE and TREND levels; green when price is on the bullish side."""
        if not lv:
            return "<td>—</td><td>—</td>"
        px, tl, tn, inv = lv

        def c(level):
            if level is None or px is None:
                return "<td>—</td>"
            bull = (px > level) != inv
            txt = f"{level:.4f}" if abs(level) < 5 else num(level)
            return f"<td class='{'up' if bull else 'dn'}'>{txt}</td>"
        return c(tl) + c(tn)

    for r in b["countries"]:
        q, i, yv, fx, rec = r["q"], r["idx"], r["yld"], r["fx"], r["rec"]
        qn = QUAD_NAME.get(q[0] or "", "")
        rcls = "up" if rec.startswith(("BUY", "LONG")) else "dn" if rec.startswith(("SHORT", "AVOID")) else ""
        rcls = "up" if rec.startswith("BUY") else "dn" if rec.startswith(("SHORT", "SELL")) else ""
        etf_last = num(i["etf_lv"][0]) if i.get("etf_lv") and i["etf_lv"][0] else ""
        rows.append(f"<td><b>{r['name']}</b><br><span class='mut'>{INDEX_NAME.get(r['name'], '')}</span></td>"
                    f"<td><b>Q{q[0] or '?'}</b> {qn} → Q{q[1] or '?'}</td>"
                    f"{pct(i['pct'])}{lv_cells(i.get('lv'))}{rng_cell(i['rp'])}"
                    f"<td><b>{r['etf'] or '—'}</b> <span class='mut'>{etf_last}</span></td>"
                    f"{lv_cells(i.get('etf_lv'))}{rng_cell(i.get('etf_rp'))}"
                    f"<td>{num(yv['last']) if yv['last'] else '—'}</td>{lv_cells(yv.get('lv'))}{rng_cell(yv['rp'])}"
                    f"<td>{e(fx['label']) or '—'}</td>{pct(fx['pct'])}{lv_cells(fx.get('lv'))}{rng_cell(fx['rp'])}"
                    f"<td class='{rcls}' style='text-align:left'><b>{e(rec)}</b></td>")
    out.append(tbl(["Country / index", f"Quad now ({qc[0]}) → next",
                    "Index", "Trade", "Trend", "Pos",
                    "US ETF · last", "Trade", "Trend", "Pos",
                    "10y", "Trade", "Trend", "Pos",
                    "Ccy", "vs $", "Trade", "Trend", "Pos", "Call"], rows))
    out.append("<div class='mut'>Trade / Trend = the line levels (green = price on the bullish side, red = bearish). Trade = Vola2vola upper line, Trend = MFR Trend band middle. Call = country ETF + currency ETF action from trend + range position; quad is context (signals win when they disagree). "
               "BUY (dip) = bullish & low in range · SHORT (bounce) = bearish & high in range.</div>")
    eb = []
    for label, r in b["euro_bonds"]:
        if r:
            d, close, mlo, mup, rlo, rhi = r
            eb.append(f"{label} {float(close):.2f} ({d:%m/%d})")
    if eb:
        out.append("<div class='mut'>Euro bond futures (price; down = yields up): " + " · ".join(eb) +
                   " · UK/JP/CA/DE 10y yields populate after the next TradingView run.</div>")

    out.append("<h2>Commodities</h2>")
    out.append(tbl(["", "Last", "Overnight", "Trend", "Range (Hedgeye)", "Position"],
                   [f"<td>{l}</td><td>{num(x)}</td>{pct(p, True)}{trend_cell(t)}"
                    f"<td>{num(lo) + ' – ' + num(hi) if lo else '—'}</td>{rng_cell(rp)}"
                    for l, x, p, t, lo, hi, rp in b["com"]]))

    (btc, bt, bb, bs, brp) = b["btc"]
    if btc:
        out.append(f"<h2>Bitcoin</h2><div>{btc[0]:,.0f} ({btc[2]:+.2f}%) · trend {bt or '—'} · "
                   f"range {num(bb)} – {num(bs)} · position {brp:.2f}</div>" if brp is not None else "")

    out.append("<h2>Your book</h2>")
    tot = []
    for acct, lng, sht, cash, d in b["acct_tot"]:
        lng, sht, cash = float(lng or 0), float(sht or 0), float(cash or 0)
        if lng or sht or cash > 100:
            ratio = f" · short {abs(sht) / lng * 100:.0f}% of longs" if lng and sht else ""
            tot.append(f"<div class='chip'><b>{e(acct)}</b>long ${lng:,.0f} · short ${abs(sht):,.0f} · cash ${cash:,.0f}{ratio}</div>")
    out.append("<div class='grid'>" + "".join(tot) + "</div>")
    brows = []
    for r in b["book_rows"]:
        acls = "dn" if r["act"].startswith(("STOP", "COVER ·")) else "up" if r["act"].startswith("ADD") else \
               "warn" if r["act"].startswith(("TRIM", "COVER SOME", "WATCH")) or " says " in r["act"] else ""
        bull = (lambda lvl: "" if lvl is None or r["px"] is None else
                ("up" if (r["px"] > lvl) != r["short"] else "dn"))
        brows.append(f"<td><b>{r['sym']}</b> <span class='mut'>{'SHORT' if r['short'] else ''}</span></td>"
                     f"<td class='mut'>{e(r['accts'] or '')}</td><td>${abs(r['mv']):,.0f}</td>{pct(r['pnl'])}"
                     f"<td>{num(r['px'])}</td>{rng_cell(r['rp'])}"
                     f"<td class='{bull(r['tl'])}'>{num(r['tl'])}</td><td class='{bull(r['tn'])}'>{num(r['tn'])}</td>"
                     f"<td class='mut' style='text-align:left'>{e(r['grp'])}</td>"
                     f"<td class='mut' style='text-align:left'>{e(r['lists'])}</td>"
                     f"<td class='{acls}' style='text-align:left'><b>{e(r['act'])}</b></td>")
    out.append(tbl(["Position", "Acct", "Value", "P/L", "Last", "Pos", "Trade", "Trend", "Group", "Hedgeye lists", "Action"], brows))
    out.append("<div class='mut'>Last = prior close (premarket starts 4:00). Trade/Trend green = position on the right side of the line "
               "(for shorts, green = price below the line). Sorted: stops first, then trims/covers, adds, watch, holds.</div>")

    out.append("<h2>If / then playbook</h2>")
    out.append(tbl(["If…", "Then…"], [f"<td>{e(a)}</td><td style='text-align:left'>{e(c)}</td>" for a, c in b["ifthen"]]))
    if b["t1a"]:
        t = b["t1a"]
        out.append(f"<div class='mut'>Tier1 Alpha ({t[0]}): gamma {t[1]}, flow {t[2]}, flip {num(float(t[3]) if t[3] else None)}, "
                   f"band {num(float(t[4]) if t[4] else None)}–{num(float(t[5]) if t[5] else None)}</div>")
    out.append("<h2>Calendar</h2><div class='mut'>Earnings and data come in from the morning emails; Claude adds them in the 5:30 session.</div>")
    out.append("<p class='mut'>Order ideas are suggestions; you place every order yourself.</p></body></html>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    b = build()
    OUT.mkdir(parents=True, exist_ok=True)
    page = render(b)
    p = OUT / f"morning_brief_{b['now']:%Y-%m-%d}.html"
    p.write_text(page, encoding="utf-8")
    (OUT / "LATEST.html").write_text(page, encoding="utf-8")
    print("\n".join(b["headline"]))
    print(p)
    if a.open:
        os.startfile(str(OUT / "LATEST.html"))


if __name__ == "__main__":
    sys.exit(main())
