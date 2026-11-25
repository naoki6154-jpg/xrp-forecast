import sys; sys.path.insert(0,"/home/claude/work")
import pandas as pd, numpy as np
pd.set_option('display.width',220); pd.set_option('display.float_format','{:.3f}'.format); pd.set_option('display.max_columns',40)
rng=np.random.default_rng(7)
w=pd.read_pickle('wf.pkl'); w=w.rename(columns={'sig_x':'sig'})
w['lo']=(w.actual<w.q10).astype(int); w['hi']=(w.actual>w.q90).astype(int); w['in80']=1-w.lo-w.hi
w['in50']=((w.actual>=w.q25)&(w.actual<=w.q75)).astype(int)
H={'1d':1,'1w':7}; w['z']=w.actual/(w.sig*np.sqrt(w.h.map(H)))
def bb(x,blk,B=2000):
    b=x.values.astype(float); n=len(b); out=[]
    for _ in range(B):
        s=rng.integers(0,n,size=int(np.ceil(n/blk))); idx=np.concatenate([np.arange(a,a+blk)%n for a in s])[:n]; out.append(b[idx].mean())
    return np.percentile(out,[2.5,97.5])
print('===== 前進型（2020-01〜2026-11）モデル混合E・Cの80%範囲の外れ、レジーム別 =====')
for m in('E','C'):
  for h,blk in(('1d',14),('1w',14)):
    g=w[(w.m==m)&(w.h==h)]
    print('\n',m,h,'全体 n=%d  下外れ %.3f 上外れ %.3f 範囲内 %.3f (期待 .1/.1/.8)  50%%内 %.3f'%(len(g),g.lo.mean(),g.hi.mean(),g.in80.mean(),g.in50.mean()))
    t=g.groupby('state').agg(n=('lo','size'),下外れ=('lo','mean'),上外れ=('hi','mean'),範囲内=('in80','mean'),zの平均=('z','mean'),zの標準偏差=('z','std'),実際の平均騰落=('actual','mean'),上昇率=('up','mean'))
    print(t.to_string())
    d=g[g.state=='下落']; o=g[g.state!='下落']
    for nm,ser in(('下外れ',d.lo),):
        print('  下落レジームの下外れ %.3f  95%%区間(ブロック14日) %s  / それ以外 %.3f  95%%区間 %s'%(ser.mean(),bb(ser,blk).round(3),o.lo.mean(),bb(o.lo,blk).round(3)))
    print('  下落レジームの上外れ %.3f / それ以外 %.3f'%(d.hi.mean(),o.hi.mean()))
print('\n===== 年ごとの下落レジームの1週間後・混合Eの下外れ =====')
g=w[(w.m=='E')&(w.h=='1w')].copy(); g['y']=g.t.dt.year
print(g[g.state=='下落'].groupby('y').agg(n=('lo','size'),下外れ=('lo','mean'),上外れ=('hi','mean'),zの平均=('z','mean'),zの標準偏差=('z','std')).to_string())
print('\n===== 下落レジームでの実現ボラ/予想σ（1週間の日次実現変動÷sig）=====')
g=w[(w.m=='E')&(w.h=='1w')]; print(g.assign(ratio=g.fwd/g.sig).groupby('state').ratio.agg(['count','mean','median']).to_string())
print('\n===== z の分位（モデルの想定: 10%点=-1.28*√(t4調整)付近。実際の分位と比べる）1週間後 混合E =====')
for st in('下落','上昇','横ばい','荒れ'):
    z=g[g.state==st].z; print(st,len(z),'z分位 5/10/25/50/75/90/95%:',np.percentile(z,[5,10,25,50,75,90,95]).round(2))
print('全体',np.percentile(g.z,[5,10,25,50,75,90,95]).round(2))
# 下落レジームの滞在期間（独立な「エピソード」数）
f=pd.read_pickle('feat.pkl'); f=f[f.index>='2020-01-01']
s=(f.state=='下落'); ep=(s&~s.shift(1,fill_value=False)).sum(); print('\n下落レジームの日数',int(s.sum()),'エピソード数(連続区間)',int(ep))
