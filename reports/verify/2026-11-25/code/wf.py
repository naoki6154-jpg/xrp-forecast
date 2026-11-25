import sys,os,time,math,json
sys.path.insert(0,'/home/claude/xrp-forecast/engine')
import numpy as np, pandas as pd
import inputs as I, models_bd as BD, technicals as TA
ROOT='/home/claude/xrp-forecast'
cfg=json.load(open(ROOT+'/engine/config.json'))
close=I.load_daily_usd(ROOT)
fgdf=pd.read_csv(ROOT+'/data/raw/sentiment/fear_greed.csv'); fgs=pd.Series(fgdf.value.astype(float).values,index=pd.to_datetime(fgdf.date))
full=BD.daily_frame(close,fgs)
lr=np.log(close).diff(); ew=lr.ewm(alpha=0.06,adjust=False).std(); rv90=lr.rolling(90).std(); sig_all=0.5*ew+0.5*rv90
ob=pd.read_csv(ROOT+'/data/raw/prices/binance_xrp_usdt_1d.csv'); ob.index=pd.to_datetime(ob.date)
def fred(n_):
    fp=ROOT+'/data/raw/fred/%s.csv'%n_
    if not os.path.exists(fp): return None
    d_=pd.read_csv(fp); return pd.Series(pd.to_numeric(d_['value'],errors='coerce').values,index=pd.to_datetime(d_.date)).dropna()
TX=TA.compute(ob[['open','high','low','close','volume']],fgs,fred('NASDAQCOM'),fred('DTWEXBGS'),fred('VIXCLS'))
TS=TA.states(TX)
# --- レジーム（engine/inputs.regime と同じ規則を、全日分ベクトルで）
c=cfg['regime']; ma=close.rolling(c['ma_days']).mean(); slope=ma/ma.shift(c['slope_lookback'])-1; dev=close/ma-1
vol30=lr.rolling(c['vol_days']).std()
pct=vol30.rolling(365,min_periods=365).apply(lambda w:(w<=w[-1]).mean(),raw=True)
state=pd.Series('横ばい',index=close.index)
state[(dev>c['range_band'])&(slope>0)]='上昇'; state[(dev<-c['range_band'])&(slope<0)]='下落'
state[(dev.abs()>c['range_band'])&(state=='横ばい')]=np.where(dev<0,'下落','上昇')[(dev.abs()>c['range_band'])&(state=='横ばい')]
state[pct>=c['crisis_pct']]='荒れ'
# RSI14 (Wilder)
d=close.diff(); up=d.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); dn=(-d.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean(); rsi=100-100/(1+up/dn)
feat=pd.DataFrame({'state':state,'dev':dev,'slope':slope,'vol30':vol30,'pct':pct,'rsi':rsi,'fg':fgs.reindex(close.index),'r7':close.pct_change(7),'r30':close.pct_change(30),'sig':sig_all})
HS={'1d':1,'1w':7}; steps=[1,7]; QS=(10,25,50,75,90); n=4000
origins=[t for t in close.index if t>=pd.Timestamp('2020-01-01')]
rows=[]; t0=time.time()
for k,t in enumerate(origins):
    pos=full.index.get_loc(t)
    if pos+7>=len(full): break
    sig=float(sig_all.iloc[pos]); 
    if not np.isfinite(sig): continue
    sub=full.iloc[:pos+1]; seed=1000+k
    rB,_,_=BD.model_b(sub,steps,n,seed,sig); rD,_=BD.model_d(sub,t,steps,n,seed+1,sig)
    tp=TX.index.searchsorted(t,side='right'); rT,_=TA.model_t(TX.iloc[:tp],TS.iloc[:tp],TX.close.iloc[:tp],steps,n,seed+3,sig)
    rng=np.random.default_rng(seed+2); rC=(rng.standard_t(4,(n,len(steps)))/math.sqrt(2))*sig*np.sqrt(np.array(steps))[None,:]
    rE=np.concatenate([rB[:2*n//7],rC[:2*n//7],rD[:n//7],rT[:2*n//7]])
    for j,(hn,h) in enumerate(HS.items()):
        actual=float(np.log(full.close.iloc[pos+h]/full.close.iloc[pos]))
        # その後h日の実現変動（日次）
        fwd=float(lr.iloc[pos+1:pos+1+h].std()) if h>1 else abs(float(lr.iloc[pos+1]))
        for mn,r in (('B',rB),('C',rC),('D',rD),('T',rT),('E',rE)):
            x=r[:,j]; qv=np.percentile(x,QS)
            rows.append(dict(t=t,h=hn,m=mn,p_up=float((x>0).mean()),up=int(actual>0),actual=actual,q10=qv[0],q25=qv[1],q75=qv[3],q90=qv[4],sig=sig,fwd=fwd))
df=pd.DataFrame(rows); df=df.merge(feat,left_on='t',right_index=True,how='left')
df.to_pickle('/home/claude/work/wf.pkl'); feat.to_pickle('/home/claude/work/feat.pkl')
print(len(df),time.time()-t0, df.t.min(), df.t.max())
