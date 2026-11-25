import sys; sys.path.insert(0,"/home/claude/work")
import pandas as pd, numpy as np
from score import prep
pd.set_option('display.width',220); pd.set_option('display.float_format','{:.4f}'.format); pd.set_option('display.max_columns',40)
df=pd.read_pickle('df.pkl')
T0=pd.Timestamp('2026-11-08 06:00',tz='Asia/Tokyo')
S=df[df['mode'].isin(['morning','night'])&(df['at']>=T0)]
S=S[S.pxT.notna()].copy(); S['up']=(S.pxT>S.s0).astype(float)
res=[]
def add(name,col,h,x):
    y=x[x[col].notna()]
    if not len(y): return
    res.append(dict(model=name,h=h,n=len(y),pmean=y[col].mean(),up=y.up.mean(),bias=y[col].mean()-y.up.mean(),brier=((y[col]-y.up)**2).mean(),vs50=((y[col]-y.up)**2).mean()-0.25,dir=((y[col]>=0.5)==(y.up==1)).mean(),pstd=y[col].std()))
for h in('1d','1w'):
    x=S[S.h==h]
    for m,c in [('A イベント型MC','p_A'),('B 類似局面','p_B'),('C オプション/変動率','p_C'),('D 季節性','p_D'),('T テクニカル','p_T'),('E 機械学習(参考)','pE'),('P チャート分析(参考)','pP'),('混合(補正前・p_up_usd)','p')]:
        add(m,c,h,x)
    x=x.copy(); x['pj']=x.pj; add('表示値(補正後・p_up_jpy)×ドル結果','pj',h,x)
    x['c']=0.5; add('常に50%','c',h,x)
    x['c']=0.4  # placeholder 
    x['c']=S[S.h==h].up.mean(); add('(後知恵)全期間の上昇率を常に出す','c',h,x)
r=pd.DataFrame(res); print(r.to_string(index=False))
# 同じ件で揃えて比較（P・Eがある件だけ）
print('\n== P・Eが記録された件に揃える')
res=[]
for h in('1d','1w'):
    x=S[(S.h==h)&S.pP.notna()&S.pE.notna()]
    for m,c in [('A','p_A'),('B','p_B'),('C','p_C'),('D','p_D'),('T','p_T'),('E','pE'),('P','pP'),('混合','p')]: add(m,c,h,x)
print(pd.DataFrame(res).to_string(index=False))
# バージョン別
print('\n== 版別 混合(補正前)と表示値')
for v,g in S.groupby('ver'):
    for h in('1d','1w'):
        y=g[g.h==h]; 
        if len(y): print(v,h,len(y),'p補正前 %.3f p表示 %.3f 実際の上昇 %.3f  Brier補正前 %.4f 表示 %.4f'%(y.p.mean(),y.pj.mean(),y.up.mean(),((y.p-y.up)**2).mean(),((y.pj-y.up)**2).mean()))
