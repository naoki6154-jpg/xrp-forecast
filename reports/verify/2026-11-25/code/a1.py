import sys; sys.path.insert(0,"/home/claude/work")
import pandas as pd, numpy as np
from score import prep,summ
pd.set_option('display.width',220); pd.set_option('display.float_format','{:.4f}'.format); pd.set_option('display.max_columns',30)
df=pd.read_pickle('df.pkl')
T0=pd.Timestamp('2026-11-08 06:00',tz='Asia/Tokyo')
S=df[df['mode'].isin(['morning','night'])&(df['at']>=T0)]
A=df[df['at']>=T0]
cols=['set','h','n','days','up','pmean','dir','brier','in50','in80','lo','hi']
print('== ① ドル建て・定時（司令塔と同じ）'); print(summ(prep(S),'USD定時')[cols])
print('== ② 予想時点の価格を、1時間足の終値（s0_bar）に替えた'); 
x=S.copy(); x=x[x.s0_bar.notna()]; print(summ(prep(x,'s0_bar'),'USD s0=1h足')[cols])
print('   s0(東京経由の現在値)と1時間足の差の平均(%)',((S.s0/S.s0_bar-1)*100).abs().mean(), '最大',((S.s0/S.s0_bar-1)*100).abs().max())
print('== ③ 手動の回も含む'); print(summ(prep(A),'USD定時+手動')[cols])
# yen
def prepj(d):
    d=d[d.jT.notna()].copy(); d['up']=(d.jT>d.j0).astype(float); d['p0']=d.p; d['p']=d.pj
    d['br']=(d.p-d.up)**2; d['dirhit']=((d.p>=0.5)==(d.up==1))
    d['in50']=[q[1]<=x<=q[3] for q,x in zip(d.qj,d.jT)]; d['in80']=[q[0]<=x<=q[4] for q,x in zip(d.qj,d.jT)]
    d['lo80']=[x<q[0] for q,x in zip(d.qj,d.jT)]; d['hi80']=[x>q[4] for q,x in zip(d.qj,d.jT)]
    d['lo']=d.lo80; d['hi']=d.hi80
    return d
def summj(d,n):
    o=[]
    for h in('1d','1w'):
        x=d[d.h==h]; o.append(dict(set=n,h=h,n=len(x),days=x.target.dt.date.nunique(),up=x.up.mean(),pmean=x.p.mean(),dir=x.dirhit.mean(),brier=x.br.mean(),in50=x.in50.mean(),in80=x.in80.mean(),lo=x.lo.mean(),hi=x.hi.mean()))
    return pd.DataFrame(o)
print('== ④ 円建て（実際の円価格＝目標時刻に最も近い予想記録の bitbank 円価格、45分以内）'); print(summj(prepj(S),'JPY定時')[cols]); print(summj(prepj(A),'JPY定時+手動')[cols])
# 円とドルで向きが違った件数
d=prepj(S); u=prep(S); m=d.merge(u[['run','h','up']],on=['run','h'],suffixes=('_j','_u')); print('円とドルで「上がった/下がった」が食い違った件:',(m.up_j!=m.up_u).sum(),'/',len(m))
# 1w 1日後別に、どの日に作った予想か
# block bootstrap for brier - 0.25
rng=np.random.default_rng(0)
def boot(x,blk,B=4000):
    x=x.sort_values('at'); v=((x.p-0.5)**2*0)  # placeholder
    b=(x.br-0.25).values; n=len(b); out=[]
    for _ in range(B):
        starts=rng.integers(0,n,size=int(np.ceil(n/blk))); idx=np.concatenate([np.arange(s,s+blk)%n for s in starts])[:n]; out.append(b[idx].mean())
    return np.percentile(out,[2.5,50,97.5])
P=prep(S)
for h,blk in(('1d',4),('1w',28)):
    x=P[P.h==h]; print(h,'ブライア-0.25 =',round((x.br-0.25).mean(),4),' ブロックブートストラップ95%区間',boot(x,blk).round(4))
