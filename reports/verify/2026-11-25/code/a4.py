import sys; sys.path.insert(0,"/home/claude/work")
import pandas as pd, numpy as np
pd.set_option('display.width',230); pd.set_option('display.float_format','{:.3f}'.format); pd.set_option('display.max_columns',40)
rng=np.random.default_rng(11)
w=pd.read_pickle('wf.pkl').rename(columns={'sig_x':'sig'})
w['lo']=(w.actual<w.q10).astype(int); w['hi']=(w.actual>w.q90).astype(int)
# ===== 実運用の下外れが「たまたま」かを、過去の同じ長さの連続区間と比べる
print('== 実運用の下外れ率が、過去の連続区間(同じ日数)でどれくらい珍しいか（混合E、起点を毎日ずらした窓）')
for h,days,live in(('1d',17,0.1667),('1w',11,0.2619)):
    g=w[(w.m=='E')&(w.h==h)].sort_values('t').reset_index(drop=True); s=g.lo.values; r=pd.Series(s).rolling(days).mean().dropna()
    dn=g.state.values; r_dn=[s[i:i+days].mean() for i in range(len(g)-days) if (dn[i:i+days]=='下落').all()]
    print(h,'窓=%d日'%days,'全窓 n=%d 下外れ率が実運用(%.0f%%)以上の窓 %.1f%%'%(len(r),live*100,100*(r>=live).mean()),'｜全日が下落レジームの窓 n=%d 以上 %.1f%%'%(len(r_dn),100*np.mean(np.array(r_dn)>=live) if r_dn else float('nan')))
# ===== 依頼4：偏りを過去データで
print('\n== 依頼4：局面別の「予想した上がる確率」と「実際に上がった割合」（前進型 2020〜）')
cal={'1d':(-0.1604416102678666,-0.20724879965232088),'1w':(-0.062206898959351664,0.6416540460568133)}
def logit(p): p=np.clip(p,0.01,0.99); return np.log(p/(1-p))
def apply_cal(p,h): a,b=cal[h]; return 1/(1+np.exp(-(a+b*logit(p))))
w['cond_all']=True
conds={'全期間':w.cond_all,'下落レジーム':w.state=='下落','上昇レジーム':w.state=='上昇','恐怖強欲≦25(極端な恐怖)':w.fg<=25,'恐怖強欲≦15':w.fg<=15,'RSI<30':w.rsi<30,'RSI<35':w.rsi<35,
 '下落＆恐怖強欲≦25':(w.state=='下落')&(w.fg<=25),'下落＆RSI<30':(w.state=='下落')&(w.rsi<30),'下落＆恐怖≦25＆RSI<30':(w.state=='下落')&(w.fg<=25)&(w.rsi<30),'30日騰落≦-20%':w.r30<=-0.20,'200日線から-20%以下':w.dev<=-0.20}
def bb(x,blk,B=1500):
    b=np.asarray(x,float); n=len(b); out=[]
    for _ in range(B):
        s=rng.integers(0,n,size=int(np.ceil(n/blk))); idx=np.concatenate([np.arange(a,a+blk)%n for a in s])[:n]; out.append(b[idx].mean())
    return np.percentile(out,[2.5,97.5])
for h,blk in(('1d',7),('1w',21)):
    print('\n--',h,'(窓の重なり補正: ブロック%d日)'%blk)
    rows=[]
    for cn,cm in conds.items():
        g=w[(w.h==h)&(w.m=='E')&cm]
        if len(g)<30: rows.append(dict(局面=cn,n=len(g))); continue
        pm=g.p_up.mean(); u=g.up.mean(); pc=apply_cal(g.p_up.values,h).mean()
        br=((g.p_up-g.up)**2).mean(); brc=((apply_cal(g.p_up.values,h)-g.up)**2).mean(); b0=((0.5-g.up)**2).mean()
        d=(g.p_up-g.up).values; ci=bb(d,blk)
        rows.append(dict(局面=cn,n=len(g),実効日数=len(g)/ (7 if h=='1w' else 1),予想p=pm,補正後p=pc,実際の上昇率=u,過大=pm-u,過大の95下=ci[0],過大の95上=ci[1],Brier_E=br,Brier_補正後=brc,Brier_50=b0))
    print(pd.DataFrame(rows).to_string(index=False))
# モデル別の過大評価（下落＆恐怖≦25）
print('\n== モデル別 予想p平均−実際上昇率（下落レジームのみ／極端な恐怖のみ）')
for h in('1d','1w'):
    for cn,cm in(('下落',w.state=='下落'),('恐怖強欲≦25',w.fg<=25),('RSI<30',w.rsi<30)):
        g=w[(w.h==h)&cm]; t=g.groupby('m').apply(lambda x: pd.Series(dict(n=len(x),予想p=x.p_up.mean(),実際=x.up.mean(),過大=x.p_up.mean()-x.up.mean(),Brier=((x.p_up-x.up)**2).mean())))
        print(h,cn); print(t.T.to_string())
# 恐怖強欲の逆張りに相当するモデルは？モデルT(テクニカル)とE(機械学習)の特徴を確認
