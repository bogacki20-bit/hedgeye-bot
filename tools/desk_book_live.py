import sys, json; sys.path.insert(0, r'C:\Users\bogac\hedgeye-bot')
sys.stdout.reconfigure(encoding='utf-8')
import db_pg, yfinance as yf
with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""select b.symbol, b.account_name, sum(b.market_value), sum(b.quantity), max(v.gics_sector), max(v.range_low), max(v.range_high),
                   max(v.tv_trade), max(v.tv_trend_short), max(v.tv_trend_long), max(v.trend_dir), max(t.hedgeye_bucket_0629),
                   (select max(side) from ss_roster_history s where s.ticker=b.symbol and s.removed_on is null)
                   from book_positions b left join v_screener v on v.ticker=b.symbol
                   left join ticker_tags t on t.ticker=b.symbol
                   where b.snapshot_date=(select max(snapshot_date) from book_positions) and abs(b.market_value)>1
                   group by 1,2 order by 1""")
    rows=cur.fetchall()
syms=sorted({r[0] for r in rows if r[0] and r[0].isalpha()})
d=yf.download(syms,period="1d",interval="5m",progress=False,threads=True)["Close"].ffill().iloc[-1]
f=lambda x: float(x) if x is not None else None
for s,acct,mv,q,sec,lo,hi,tr,mu,ml,td,bk,ss in rows:
    p=d.get(s); p=None if p is None or p!=p else float(p)
    lo,hi,tr,mu,ml=map(f,(lo,hi,tr,mu,ml))
    side="S" if (q is not None and float(q)<0) else "L"
    tn=(mu if side=="S" else ml) or mu or ml   # card: longs vs the cloud bottom, shorts vs the top (11/15)
    rp=(p-lo)/(hi-lo) if p and lo and hi and hi>lo else None
    print(f"{s:6}{acct[:4]:5}{side} mv{float(mv):7.0f} px{p or 0:8.2f} rp{rp if rp is not None else -9:5.2f} tr{tr or 0:8.2f} tn{tn or 0:8.2f} {td or '-':8} PM:{(bk or '-')[:14]:15}SS:{ss or '-':6}{(sec or '')[:14]}")
