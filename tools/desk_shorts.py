"""Short-book recycling sheet: per short, live P&L plus the pre-set cover / re-short limit levels.

Rules (short-recycling-rules memory):
  cover 1/3 at +3% or rp<=0.25 · cover 1/3 more at the range low · hold last 1/3 to the trade line
  short above trade -> cover half · above trend -> cover all
  re-short (reload) on a 3+ day green bounce into rp>=0.65 while still below trade; one add max
"""
import sys; sys.path.insert(0, r'C:\Users\bogac\hedgeye-bot'); sys.stdout.reconfigure(encoding='utf-8')
import db_pg, yfinance as yf, datetime as dt, math, warnings; warnings.filterwarnings("ignore")

with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""select symbol, sum(quantity), sum(cost_basis), sum(market_value) from book_positions
                   where snapshot_date=(select max(snapshot_date) from book_positions) and quantity<0 group by 1""")
    shorts={r[0]:tuple(float(x or 0) for x in r[1:]) for r in cur.fetchall()}
    cur.execute("select ticker, range_low, range_high, tv_trade, tv_trend, tv_trend from v_screener where ticker=any(%s)",(list(shorts),))
    lv={r[0]:[float(x) if x is not None else None for x in r[1:]] for r in cur.fetchall()}
    cur.execute("""select distinct on (ticker, side) ticker, side, removed_on from ss_roster_history
                   where ticker=any(%s) order by ticker, side, added_on desc""",(list(shorts),))
    ss={}
    for t,s,r in cur.fetchall():
        if s=='short': ss[t]="SS:S" if r is None else f"SSout{r:%m/%d}"
    # on the Position Monitor short side -> SS removal is managed by the lines instead of a full cover
    cur.execute("select ticker from ticker_tags where ticker=any(%s) and hedgeye_bucket_0629 in ('active_short','top_idea_short','short_bench')",(list(shorts),))
    pm={r[0] for r in cur.fetchall()}

from tools.range_structure import structure
RS=structure(list(shorts))
px={}
for t in shorts:
    try:
        h=yf.Ticker(t).history(period="5d",interval="1d")
        px[t]=(float(h.Close.iloc[-1]), float(h.Close.iloc[-2]))
    except Exception: pass

def r2(x): return f"{x:8.2f}" if x else "     n/a"
print(f"shorts as of {dt.datetime.now():%m/%d %H:%M}")
print(f"{'tkr':6}{'qty':>5}{'avg':>9}{'px':>9}{'day':>7}{'P&L':>7}{'rp':>6}  {'cov1/3 +3%':>10}{'cov1/3 lo':>10}{'reload .65':>11}{'stop tr':>9}{'trend':>9}  ss       range  action")
rows=[]
for t,(q,cb,mv) in shorts.items():
    q=abs(q); avg=cb/q if q else 0
    p,prev=px.get(t,(abs(mv)/q if q else 0, None))
    lo,hi,tr,mu,ml=(lv.get(t) or [None]*5)
    tn=(mu+ml)/2 if mu and ml else None
    rp=(p-lo)/(hi-lo) if lo and hi and hi>lo else None
    pnl=100*(avg/p-1) if p else 0
    c3=avg*0.97
    reload=lo+0.65*(hi-lo) if lo and hi else None
    third=max(1,math.floor(q/3)) if q>=2 else 0
    if ss.get(t,"").startswith("SSout") and t not in pm: act="COVER ALL (removed from SS short list)"
    elif tn and p>tn: act="COVER ALL (above trend)"
    elif tr and p>tr: act=f"COVER HALF {math.ceil(q/2)} (above trade)"
    elif third and lo and p<=lo: act=f"COVER {third} (at range low)"
    elif third and (pnl>=3 or (rp is not None and rp<=0.25)): act=f"COVER {third} (+3%/rp<=.25)"
    elif rp is not None and rp>=0.65 and pnl<0: act="RELOAD zone (add once, green day 3)"
    else: act="hold"
    day=f"{100*(p/prev-1):+6.1f}%" if prev else "      "
    rows.append((pnl,f"{t:6}{q:5.0f}{avg:9.2f}{p:9.2f}{day}{pnl:+6.1f}%{(rp if rp is not None else float('nan')):6.2f}  {r2(c3):>10}{r2(lo):>10}{r2(reload):>11}{r2(tr):>9}{r2(tn):>9}  {ss.get(t,'-'):8} {RS.get(t,{}).get('tag','  -  ')}  {act}"))
for _,l in sorted(rows,reverse=True): print(l)
