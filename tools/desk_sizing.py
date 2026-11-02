"""Position-sizing audit against Master The Market MIN/MAX rules (docs/playbook/RULE_CARD.md).

Per position: % of its account, asset class, MIN/MAX, flag (UNDER MIN / OVER MAX / AT MAX -> 6% stop),
plus the 50-150 bp buy/sell increment in dollars for that account.
Account value comes from Fidelity's own % of account column (includes pending cash).
"""
import sys; sys.path.insert(0, r'C:\Users\bogac\hedgeye-bot'); sys.stdout.reconfigure(encoding='utf-8')
import db_pg

# (min %, max %) — MTM ch. 4
LIMITS = {"commodity": (1, 4), "equity_etf": (2, 6), "fixed_income": (3, 10), "stock_long": (1, 3), "short": (0.5, 2)}
COMMODITY = {"BNO", "HGER", "BWET", "USO", "GLD", "IAU", "AAAU", "SLV", "DBC", "XOP", "IBIT", "ETHA"}
# bond, cash-like and currency ETFs (currencies sized like fixed income: low vol)
FIXED_INC = {"CLOX", "ILS", "BNDD", "TLT", "IEF", "SHY", "UUP", "FXY", "FXC", "FXF", "FXE", "FXB", "BNDX", "HYG", "LQD"}
EQUITY_ETF = {"XLV", "XLP", "XLE", "XLU", "XLI", "XLK", "XLF", "RSP", "SPY", "QQQ", "IWM", "BUG", "HTEC", "IAK", "EWW", "EWL",
              "EWC", "EWA", "LVHI", "SMH", "IGV", "SCHA", "ISCG", "USD", "PSQ", "SH", "RWM"}
INVERSE_1X = {"NVDD", "METD", "PSQ", "SH", "RWM", "MSFD", "TSLQ"}   # long a 1x inverse = a short, sized as one
CASH = ("SPAXX", "FDRXX", "CORE", "QPCTQ", "BUXX")   # BUXX = ultra-short cash parking, not sized


def asset_class(sym, qty):
    if qty < 0 or sym in INVERSE_1X: return "short"
    if sym in COMMODITY: return "commodity"
    if sym in FIXED_INC: return "fixed_income"
    if sym in EQUITY_ETF: return "equity_etf"
    return "stock_long"


def audit():
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select account_name, symbol, sum(quantity), sum(market_value), max(pct_of_account), sum(cost_basis)
                       from book_positions where snapshot_date=(select max(snapshot_date) from book_positions)
                       group by 1, 2""")
        rows = cur.fetchall()
    acct_val, pos = {}, []
    for a, s, q, mv, pct, cb in rows:
        mv = float(mv or 0); pct = float(pct or 0)
        if pct and abs(mv) > 50: acct_val[a] = max(acct_val.get(a, 0), abs(mv) / abs(pct) * 100)
        if s and not s.startswith(CASH) and q:
            pos.append((a, s.rstrip("*"), float(q), mv, float(cb or 0)))
    out = []
    for a, s, q, mv, cb in pos:
        v = acct_val.get(a)
        if not v: continue
        cls = asset_class(s, q); lo, hi = LIMITS[cls]
        w = 100 * abs(mv) / v
        flag = "BUILDING" if w < lo * 0.9 else "OVER MAX" if w > hi * 1.05 else "AT MAX (6% stop)" if w >= hi * 0.9 else ""
        pnl = (abs(cb) - abs(mv)) / abs(cb) * 100 if q < 0 and cb else ((mv - cb) / cb * 100 if cb else 0)
        out.append(dict(acct=a, sym=s, cls=cls, w=w, lo=lo, hi=hi, mv=mv, v=v, flag=flag, pnl=pnl,
                        to_min=max(0, lo * v / 100 - abs(mv)), to_max=max(0, hi * v / 100 - abs(mv))))
    return acct_val, out


if __name__ == "__main__":
    vals, out = audit()
    for a in sorted(vals):
        v = vals[a]
        print(f"\n== {a}  value ${v:,.0f}  ·  increment 50-150bp = ${v*.005:,.0f}-{v*.015:,.0f}")
        for r in sorted((r for r in out if r["acct"] == a), key=lambda r: (r["flag"] == "", r["cls"], -r["w"])):
            # next tranche = one 50-150bp step, capped at what's left to MIN
            step = min(r["to_min"], r["v"] * .01)
            extra = f"  next tranche ~${step:,.0f} (${r['to_min']:,.0f} to min)" if r["flag"] == "BUILDING" else ""
            if r["flag"] == "AT MAX (6% stop)": extra = f"  P&L {r['pnl']:+.1f}% (stop -6%)"
            print(f"  {r['sym']:7}{r['cls']:13}{r['w']:5.1f}%  [{r['lo']}-{r['hi']}%]  ${abs(r['mv']):7,.0f}  {r['flag']}{extra}")
