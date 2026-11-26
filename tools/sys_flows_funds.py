"""tools/sys_flows_funds.py - real daily futures books of three managed-futures ETFs.

Not a model: each fund prints what it holds. Three funds, three different rule sets:
  DBMF  (iMGP DBi)        copies the big CTA index from its returns; ~10 markets
  KMLM  (KraneShares)     Mount Lucas trend index; bonds, currencies, commodities, no stocks
  CTA   (Simplify)        Altis models; commodities and rates
When all three sit the same way in a market, that is observed positioning. The change from one
holdings date to the next is actual buying or selling.

Every row: (fund, as_of, security, cusip, ticker, quantity, market_value, weight_pct)
  ticker is set only on futures lines; weight_pct = futures notional as % of the fund, - = short.
Files are read in memory with the standard library only (no spreadsheet package).
"""
from __future__ import annotations

import csv
import io
import os
import re
import urllib.request
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree

from tools.sys_flows_markets import BY_KEY, MARKETS, market_of

FUNDS = ["DBMF", "KMLM", "CTA"]
DBMF_URL = "https://www.imgp.com/us/fund/US53700T8273-imgp-dbi-managed-futures-strategy-etf/"
KMLM_URL = "https://kraneshares.com/csv/{d:%m_%d_%Y}_kmlm_holdings.csv"
SIMPLIFY_PAGE = "https://www.simplify.us/etfs/cta-simplify-managed-futures-strategy-etf"
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=60).read()


def _num(txt):
    t = re.sub(r"[^0-9.eE\-]", "", str(txt) if txt is not None else "")
    try:
        return float(t)
    except ValueError:
        return None


def fetch_dbmf():
    html = _get(DBMF_URL).decode("utf-8", "ignore")
    m = re.search(r'<table id="breakdown-holdings-us".*?</table>', html, re.S)
    rows = []
    for tr in re.findall(r'<tr class="holding row">(.*?)</tr>', m.group(0) if m else "", re.S):
        c = {k: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", v)).strip()
             for k, v in re.findall(r'<td class="([a-z_]+)">(.*?)</td>', tr, re.S)}
        try:
            as_of = datetime.strptime(c.get("value_date", ""), "%m/%d/%Y").date()
        except ValueError:
            continue
        w = _num(c.get("weight"))
        rows.append(("DBMF", as_of, c.get("security_name", ""), c.get("isin", "") or "",
                     None if c.get("ticker") in (None, "", "-") else c["ticker"],
                     _num(c.get("shares_qty")), _num(c.get("market_value")),
                     None if w is None else round(w * 100, 2)))
    return rows


def fetch_kmlm(today=None):
    """The file is named by date: walk back from today to the latest one posted."""
    today = today or datetime.now().date()
    text = None
    for back in range(0, 8):
        try:
            text = _get(KMLM_URL.format(d=today - timedelta(days=back))).decode("utf-8-sig", "ignore")
            if "Holdings" in text[:200]:
                break
            text = None
        except Exception:
            continue
    if not text:
        return []
    lines = list(csv.reader(io.StringIO(text)))
    m = re.search(r"(\d{4}-\d{2}-\d{2})", " ".join(lines[0]))
    as_of = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    hdr = lines[1]
    recs = [dict(zip(hdr, r)) for r in lines[2:] if len(r) >= len(hdr)]
    nav = sum(_num(r["Market Value($)"]) or 0.0 for r in recs)
    rows = []
    for r in recs:
        fut = r["Type"].strip().lower() == "future"
        notional = _num(r["Notional Value($)"]) if fut else _num(r["Market Value($)"])
        ident = "" if r["Identifier"].strip() in ("", "-", "–") else r["Identifier"].strip()
        rows.append(("KMLM", as_of, re.sub(r"\s+", " ", r["Company Name"]).strip(), ident,
                     r["Ticker"].strip() if fut and r["Ticker"].strip() else None,
                     _num(r["Shares Held"]), notional,
                     round(100 * notional / nav, 2) if nav and notional is not None else None))
    return rows


def _xlsx_rows(blob):
    """First sheet of an .xlsx as a list of {column letter: text}."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).findall(_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    out = []
    for row in ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml")).iter(_NS + "row"):
        d = {}
        for c in row.findall(_NS + "c"):
            col = re.sub(r"\d", "", c.get("r", ""))
            v = c.find(_NS + "v")
            if c.get("t") == "inlineStr":
                d[col] = "".join(t.text or "" for t in c.iter(_NS + "t"))
            elif v is not None:
                d[col] = shared[int(v.text)] if c.get("t") == "s" else v.text
        out.append(d)
    return out


def fetch_simplify_cta():
    page = _get(SIMPLIFY_PAGE).decode("utf-8", "ignore")
    m = re.search(r'href="(https://www\.simplify\.us/sites/default/files/excel_holdings/[^"]+\.xlsx)"', page)
    if not m:
        return []
    sheet = _xlsx_rows(_get(m.group(1)))
    as_of = None
    for v in sheet[0].values():
        try:
            as_of = datetime.strptime(str(v).strip(), "%m/%d/%Y").date()
        except ValueError:
            pass
    if as_of is None:
        return []
    rows = []
    for r in sheet:
        if r.get("A") != "CTA":
            continue
        fut = (r.get("U") or "").strip().lower() == "future"
        w = _num(r.get("J"))
        rows.append(("CTA", as_of, re.sub(r"\s+", " ", r.get("B", "")).strip(), (r.get("F") or "").strip(),
                     re.sub(r"\s*Comdty$", "", r.get("C", "")).strip() if fut else None,
                     _num(r.get("G")), _num(r.get("I")), None if w is None else round(w * 100, 2)))
    return rows


FETCHERS = {"DBMF": fetch_dbmf, "KMLM": fetch_kmlm, "CTA": fetch_simplify_cta}


def fetch_all():
    """{fund: rows}; a fund that fails comes back as [] with the reason in errors."""
    out, errors = {}, {}
    for fund, fn in FETCHERS.items():
        try:
            out[fund] = fn()
            if not out[fund]:
                errors[fund] = "no holdings found"
        except Exception as e:
            out[fund], errors[fund] = [], str(e)[:80]
    return out, errors


def save(conn, by_fund):
    cur = conn.cursor()
    for fund, rows in by_fund.items():
        for r in rows:
            cur.execute("""INSERT INTO sys_flow_fund_holdings (fund, as_of, security, cusip, ticker, quantity,
                             market_value, weight_pct, market) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (fund, as_of, security, cusip) DO UPDATE SET ticker=EXCLUDED.ticker,
                             quantity=EXCLUDED.quantity, market_value=EXCLUDED.market_value,
                             weight_pct=EXCLUDED.weight_pct, market=EXCLUDED.market, captured_at=now()""",
                        r + (market_of(r[2]) if r[4] else None,))
        if rows:
            os.makedirs("data/sys_flows", exist_ok=True)
            with open(f"data/sys_flows/{fund.lower()}_{rows[0][1]}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["fund", "as_of", "security", "cusip", "ticker", "quantity", "market_value", "weight_pct"])
                w.writerows(rows)
    conn.commit()


def by_market(rows):
    """One fund's rows -> ({market key: weight %}, [unmapped futures names])."""
    w, unmapped = {}, []
    for _, _, sec, _, tk, _, _, wt in rows:
        if not tk or wt is None:
            continue
        k = market_of(sec)
        if k is None:
            unmapped.append(sec)
        else:
            w[k] = w.get(k, 0.0) + wt
    return w, unmapped


def previous(conn, fund, as_of):
    """({market: weight}, date) for the fund's holdings date before as_of, from the bot."""
    try:
        cur = conn.cursor()
        cur.execute("""SELECT as_of, market, sum(weight_pct) FROM sys_flow_fund_holdings
                       WHERE fund=%s AND market IS NOT NULL AND as_of =
                         (SELECT max(as_of) FROM sys_flow_fund_holdings WHERE fund=%s AND as_of < %s)
                       GROUP BY 1, 2""", (fund, fund, as_of))
        rows = cur.fetchall()
        return {m: float(w) for _, m, w in rows if w is not None}, (rows[0][0] if rows else None)
    except Exception:
        conn.rollback()
        return {}, None


def positions(conn, by_fund):
    """{market: {fund: weight}}, {market: {fund: change}}, {fund: as_of}, [notes]."""
    pos, chg, dates, notes = {}, {}, {}, []
    for fund in FUNDS:
        rows = by_fund.get(fund) or []
        if not rows:
            continue
        dates[fund] = rows[0][1]
        w, unmapped = by_market(rows)
        if unmapped:
            notes.append(f"{fund}: not mapped to a market: " + ", ".join(sorted(set(unmapped)))[:120])
        pw, pd_ = previous(conn, fund, rows[0][1])
        for k, v in w.items():
            pos.setdefault(k, {})[fund] = v
        if pd_:
            for k in set(w) | set(pw):
                d = w.get(k, 0.0) - pw.get(k, 0.0)
                if abs(d) >= 1:
                    chg.setdefault(k, {})[fund] = d
    return pos, chg, dates, notes


__all__ = ["FUNDS", "MARKETS", "BY_KEY", "fetch_all", "save", "positions"]
