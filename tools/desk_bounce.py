import sys; sys.path.insert(0, r'C:\Users\bogac\hedgeye-bot'); sys.stdout.reconfigure(encoding='utf-8')
import db_pg, yfinance as yf, datetime as dt
with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""select w.ticker, w.layer, v.range_low, v.range_high, v.tv_trade, v.tv_trend, v.tv_trend, v.trend_dir
                   from (select unnest(%s) ticker, null::text layer) w left join v_screener v on v.ticker=w.ticker""", (sys.argv[1:],))
    rows=cur.fetchall()
now=dt.datetime.now(); mins=max(1,(now.hour*60+now.minute)-(9*60+30))
for t,layer,lo,hi,tr,mu,ml,td in rows:
    try:
        h=yf.Ticker(t).history(period="1mo"); p=h.Close.iloc[-1]; v20=h.Volume.iloc[-21:-1].mean()
        exp=v20*(0.25*min(1,mins/60)+(0.75*(mins-60)/330 if mins>60 else 0))
        cl=h.Close.values; up=0
        for k in range(len(cl)-1,0,-1):
            if cl[k]>cl[k-1]: up+=1
            else: break
        rp=(p-float(lo))/(float(hi)-float(lo)) if lo and hi else None
        tn=(float(mu)+float(ml))/2 if mu and ml else None
        print(f"{t:6}{(layer or '')[:10]:11}{p:8.2f} {100*(p/cl[-2]-1):+5.1f}% rp {rp if rp is not None else -9:5.2f} tr {float(tr) if tr else 0:8.2f} tn {tn or 0:8.2f} {td or '-':8} upDays {up} vol {h.Volume.iloc[-1]/exp:4.2f}x")
    except Exception as e: print(t, "err", e)
