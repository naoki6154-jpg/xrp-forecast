import json,glob,os,sys,csv
from datetime import datetime,timedelta,timezone
import pandas as pd, numpy as np
R='/home/claude/xrp-forecast'
JST=timezone(timedelta(hours=9))
# 1h prices (USD)
h={}
for r in csv.DictReader(open(R+'/data/raw/prices/binance_xrp_usdt_1h.csv')):
    h[r['time'][:13]]=float(r['close'])
spot=[(datetime.fromisoformat(r['time'].replace('Z','+00:00')),float(r['lastPrice'])) for r in csv.DictReader(open(R+'/data/raw/jp_relay/binance_spot_24h_xrp.csv'))]
def px(t):
    u=t.astimezone(timezone.utc); k=(u-timedelta(hours=1)).strftime('%Y-%m-%dT%H')
    if k in h: return h[k]
    near=[p for (x,p) in spot if abs((x-u).total_seconds())<=90*60]
    return near[-1] if near else None
recs=[]
for fn in sorted(glob.glob(R+'/forecasts/2026-*.json')):
    d=json.load(open(fn)); recs.append(d)
# record table
rt=[]
for d in recs:
    rt.append(dict(run=d['run_id'],at=datetime.fromisoformat(d['at']),mode=d['mode'],ver=d['engine_version'],usd=d['spot']['usd'],jpy=d['spot']['jpy'],regime=d['regime']['state'],
      dev200=d['regime']['dev_from_ma200'],vol30=d['regime']['vol30_daily'],ewma=d['vol']['ewma_daily'],fng=None,rsi=None))
rt=pd.DataFrame(rt)
rows=[]
for d in recs:
    at=datetime.fromisoformat(d['at'])
    for f in d['forecasts']:
        if f['id'] not in('1d','1w'): continue
        row=dict(run=d['run_id'],at=at,mode=d['mode'],ver=d['engine_version'],h=f['id'],target=datetime.fromisoformat(f['target']),
          s0=d['spot']['usd'],j0=d['spot']['jpy'],p=f['p_up_usd'],pj=f['p_up_jpy'],q=f['q_usd'],qj=f['q_jpy'],
          pP=f.get('model_p'),pbefP=f.get('p_up_before_P'),regime=d['regime']['state'],vol=d['vol']['ewma_daily'],
          volw=d['vol']['window_daily'],vol30=d['regime']['vol30_daily'],dev200=d['regime']['dev_from_ma200'],
          spread=f.get('model_spread'),pE=(d.get('model_e') or {}).get('per_h',{}).get(f['id'],{}).get('p_up'),
          fng=next((x['now'] for x in (d.get('model_e') or {}).get('features',[]) if x['name']=='恐怖強欲指数'),None),
          r7=next((x['now'] for x in (d.get('model_e') or {}).get('features',[]) if x['name']=='7日の騰落'),None),
          r30=next((x['now'] for x in (d.get('model_e') or {}).get('features',[]) if x['name']=='30日の騰落'),None),
          hmm=d.get('regime_hmm',{}).get('now'))
        for m,v in f['models'].items():
            row['p_'+m]=v['p_up_usd']; row['q_'+m]=v['q_usd']
        rows.append(row)
df=pd.DataFrame(rows)
df['pxT']=[px(t) for t in df['target']]
# independent s0 from 1h bar before record time
df['s0_bar']=[px(t) for t in df['at']]
# yen actual: nearest record jpy within 45 min of target
jt=rt[['at','jpy']].sort_values('at')
def jnear(t):
    dd=(jt['at']-t).abs(); i=dd.idxmin()
    return jt.loc[i,'jpy'] if dd[i]<=timedelta(minutes=45) else np.nan
df['jT']=[jnear(t) for t in df['target']]
df.to_pickle('/home/claude/work/df.pkl'); rt.to_pickle('/home/claude/work/rt.pkl')
print(len(df), df.pxT.notna().sum(), df.jT.notna().sum())
print(df.groupby(['mode']).size())
print(rt.groupby(['mode']).size()); print(rt['at'].min(),rt['at'].max())
