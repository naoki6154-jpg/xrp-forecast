import pandas as pd, numpy as np
df=pd.read_pickle('df.pkl'); rt=pd.read_pickle('rt.pkl')
def prep(d,s0col='s0',pxcol='pxT'):
    d=d.copy(); d=d[d[pxcol].notna()]
    d['up']=(d[pxcol]>d[s0col]).astype(float)
    d['br']=(d.p-d.up)**2
    d['dirhit']=((d.p>=0.5)==(d.up==1))
    d['in50']=[q[1]<=x<=q[3] for q,x in zip(d.q,d[pxcol])]
    d['in80']=[q[0]<=x<=q[4] for q,x in zip(d.q,d[pxcol])]
    d['lo80']=[x<q[0] for q,x in zip(d.q,d[pxcol])]
    d['hi80']=[x>q[4] for q,x in zip(d.q,d[pxcol])]
    return d
def summ(d,name=''):
    out=[]
    for h in('1d','1w'):
        x=d[d.h==h]
        out.append(dict(set=name,h=h,n=len(x),days=x.target.dt.date.nunique(),up=x.up.mean(),pmean=x.p.mean(),dir=x.dirhit.mean(),brier=x.br.mean(),in50=x.in50.mean(),in80=x.in80.mean(),lo=x.lo80.mean(),hi=x.hi80.mean()))
    return pd.DataFrame(out)
if __name__=='__main__':
    pd.set_option('display.width',200); pd.set_option('display.float_format','{:.4f}'.format)
    sched=df[df['mode'].isin(['morning','night'])&(df['at']>=pd.Timestamp('2026-11-08 06:00',tz='Asia/Tokyo'))]
    print(summ(prep(sched),'定時(司令塔と同じ)'))
    print(summ(prep(df[df['at']>=pd.Timestamp('2026-11-08 06:00',tz='Asia/Tokyo')]),'手動含む'))
    print(summ(prep(df),'11/7含む全部'))
