import sys, json; sys.path.insert(0, r'C:\Users\bogac\hedgeye-bot'); sys.stdout.reconfigure(encoding='utf-8')
import db_pg, yfinance as yf, datetime as dt, warnings; warnings.filterwarnings("ignore")
SECT="XLK XLY XLC XLI XLB XLE XLF XLV XLP XLU XLRE SMH IGV XBI KRE XHB XRT XOP OIH GLD TLT UUP IWM SPY QQQ".split()
STALK_L="AFRM MA V CFG COF CPAY GPN MCO PYPL SPGI XYZ EXPGY AMZN TSCO ULTA BBWI CHWY ETSY KMX BBY ELF KDP EWW EWL UNH LLY MCK".split()
STALK_S="ALLY CACC COMP EFX FAF FCFS FICO FNF OMF OPEN RKT SOFI TRU WFC ZG HOOD SYF AXP CPRI LULU TGT BOOT DECK DKS M NKE OLLI ONON SIG VFC CVNA YETI".split()
with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""select b.symbol, string_agg(distinct left(b.account_name,4),'/'), sum(b.market_value), sum(b.quantity)
                   from book_positions b where b.snapshot_date=(select max(snapshot_date) from book_positions) and abs(b.market_value)>1 group by 1""")
    book={r[0]:r[1:] for r in cur.fetchall() if r[0] and r[0].isalpha() and len(r[0])<=5}
    cur.execute("select ticker from ai_short_watch"); AI=[r[0] for r in cur.fetchall()]
    # live Signal Strength roster: latest record per ticker+side decides active vs removed
    cur.execute("""select distinct on (ticker, side) ticker, side, added_on, removed_on from ss_roster_history
                   order by ticker, side, added_on desc""")
    ss=cur.fetchall()
    SS_ON={(t,s) for t,s,a,r in ss if r is None}
    SS_OFF={(t,s):r for t,s,a,r in ss if r is not None and (t,s) not in SS_ON}
    cur.execute("""select ticker, side, added_on, removed_on from ss_roster_history
                   where added_on>=current_date-2 or removed_on>=current_date-2 order by coalesce(removed_on,added_on) desc, side, ticker""")
    SS_CHG=cur.fetchall()
    # stalk = manual list minus SS removals on that side, plus every active SS name on that side
    STALK_L=[t for t in STALK_L if (t,'long') not in SS_OFF]+sorted(t for t,s in SS_ON if s=='long' and t not in STALK_L)
    STALK_S=[t for t in STALK_S if (t,'short') not in SS_OFF]+sorted(t for t,s in SS_ON if s=='short' and t not in STALK_S)
    allt=sorted(set(SECT+STALK_L+STALK_S+list(book)+AI))
    cur.execute("select ticker, range_low, range_high, tv_trade, tv_mfr_upper, tv_mfr_lower, trend_dir, gics_sector from v_screener where ticker = any(%s)",(allt,))
    lv={r[0]:r[1:] for r in cur.fetchall()}
from tools.range_structure import structure
RS=structure(allt)
now=dt.datetime.now(); mins=max(1,(now.hour*60+now.minute)-(9*60+30)); frac=min(1,0.25*min(1,mins/60)+(0.75*(mins-60)/330 if mins>60 else 0))
if now.weekday()>=5 or now.hour*60+now.minute<9*60+30: frac=1   # market closed: the last bar is a finished session
d=yf.download(allt,period="2mo",interval="1d",progress=False,threads=True,group_by="ticker")
f=lambda x: float(x) if x is not None else None
out={}
for t in allt:
    try:
        h=d[t].dropna(); cl=h.Close.values; vo=h.Volume.values.astype(float); vo[-1]=vo[-1]/frac
        dirn=1 if cl[-1]>cl[-2] else -1; n=0
        for k in range(len(cl)-1,0,-1):
            if (cl[k]>cl[k-1])==(dirn==1) and cl[k]!=cl[k-1]: n+=1
            else: break
        sv=vo[-n:] if n else vo[-1:]
        v20=vo[-21:-1].mean()
        dec= n>=2 and sv[-1]<sv[0] and sv[-1]<v20
        lo,hi,tr,mu,ml,td,sec=(lv.get(t) or (None,)*7); lo,hi,tr,mu,ml=map(f,(lo,hi,tr,mu,ml))
        p=float(cl[-1]); tn=(mu+ml)/2 if mu and ml else None
        rp=(p-lo)/(hi-lo) if lo and hi and hi>lo else None
        out[t]=dict(p=p,chg=100*(cl[-1]/cl[-2]-1),streak=dirn*n,vol=[round(x/v20,2) for x in sv],dec=dec,rp=rp,tr=tr,tn=tn,td=td,sec=sec,
                    r5=100*(cl[-1]/cl[-6]-1),r20=100*(cl[-1]/cl[-21]-1))
    except Exception as e: out[t]={"err":str(e)}
def line(t,tag=""):
    o=out.get(t,{})
    if "err" in o or not o: return f"{t:6} err"
    a="ABV" if o['tr'] and o['p']>o['tr'] else ("BLW" if o['tr'] else "  ?")
    rp=f"{o['rp']:.2f}" if o['rp'] is not None else "  ? "
    sst="SS:L " if (t,'long') in SS_ON else "SS:S " if (t,'short') in SS_ON else \
        f"SSout{SS_OFF.get((t,'long')) or SS_OFF.get((t,'short')):%m/%d} " if ((t,'long') in SS_OFF or (t,'short') in SS_OFF) and \
        max(x for x in (SS_OFF.get((t,'long')),SS_OFF.get((t,'short'))) if x)>=dt.date.today()-dt.timedelta(days=7) else "      "
    rs=RS.get(t,{}).get('tag','  -  ')
    return f"{t:6}{tag:14}{sst}{rs} {o['p']:9.2f} {o['chg']:+5.1f}% 5d{o['r5']:+5.1f} 20d{o['r20']:+6.1f} rp {rp} {a} tr {o['tr'] or 0:8.2f} tn {o['tn'] or 0:8.2f} {o['td'] or '-':8} streak {o['streak']:+d} vol {o['vol']} {'DECEL' if o['dec'] else ''}"
print(f"time {now:%H:%M} frac {frac:.2f}")
print("== SS CHANGES (2d)"); [print(f"  {'OUT' if r else 'IN '} {s:5} {t:6} {(r or a):%m/%d}") for t,s,a,r in SS_CHG]
print("== SECTORS"); [print(line(t)) for t in SECT]
print("== BOOK")
for t,(ac,mv,q) in sorted(book.items(), key=lambda x:-abs(float(x[1][1]))):
    print(line(t, f"{ac[:9]} {'S' if q and float(q)<0 else 'L'}{float(mv):6.0f}"))
print("== STALK LONG"); [print(line(t)) for t in STALK_L if t not in book]
print("== STALK SHORT"); [print(line(t)) for t in STALK_S if t not in book]
print("== AI WATCH"); [print(line(t)) for t in AI if t not in book]
