import sys; sys.path.insert(0,"/home/claude/work")
import pandas as pd, numpy as np, math
pd.set_option('display.width',220); pd.set_option('display.float_format','{:.3f}'.format); pd.set_option('display.max_columns',40)
rng=np.random.default_rng(1)
# ===== A. 実運用 70回：1週間後の範囲の外れの中身
df=pd.read_pickle('df.pkl'); T0=pd.Timestamp('2026-11-08 06:00',tz='Asia/Tokyo')
S=df[df['mode'].isin(['morning','night'])&(df['at']>=T0)&df.pxT.notna()].copy()
S['ret']=np.log(S.pxT/S.s0); H=S.h.map({'1d':1,'1w':7})
S['sigh']=S.vol*np.sqrt(H)            # ewma日次σ×√日数（目安）
S['z']=S.ret/S.sigh
for h in('1d','1w'):
    x=S[S.h==h]
    print('\n[実運用]',h,'n',len(x),' 実際の対数騰落 平均 %.3f  中央 %.3f  z=騰落/(EWMAσ√h): 平均 %.2f 標準偏差 %.2f'%(x.ret.mean(),x.ret.median(),x.z.mean(),x.z.std()))
    # 範囲の中心のずれ（中央値q50と実際）
    x=x.assign(med=[math.log(q[2]/s) for q,s in zip(x.q,x.s0)],w80=[math.log(q[4]/q[0]) for q in x.q])
    print('   予想の中央値の騰落 平均 %.3f / 80%%幅(対数) 平均 %.3f / 実際-中央値 平均 %.3f'%(x.med.mean(),x.w80.mean(),(x.ret-x.med).mean()))
    print('   外れた件の内訳: 下に外れ %d 上に外れ %d (件数 %d)'%(int((x.pxT<[q[0] for q in x.q]).sum()),int((x.pxT>[q[4] for q in x.q]).sum()),len(x)))
# 各モデルの80%範囲の外れ
print('\n[実運用] モデル別 80%範囲の下外れ/上外れ割合')
for h in('1d','1w'):
    x=S[S.h==h]
    for m in 'ABCDT':
        y=x[x['q_'+m].notna()]
        lo=np.mean([p<q[0] for p,q in zip(y.pxT,y['q_'+m])]); hi=np.mean([p>q[4] for p,q in zip(y.pxT,y['q_'+m])]); w=np.mean([math.log(q[4]/q[0]) for q in y['q_'+m]])
        print('  ',h,m,'n',len(y),'下外れ %.0f%% 上外れ %.0f%% 幅 %.3f'%(lo*100,hi*100,w))
# 実運用の「実現変動」対「予想σ」：予想後h日の1時間足から日次リターンの標準偏差
# 日次(JST 1日)の実現変動を価格1時間足で
import csv
from datetime import datetime,timezone,timedelta
hh=pd.read_csv('/home/claude/xrp-forecast/data/raw/prices/binance_xrp_usdt_1h.csv'); hh['t']=pd.to_datetime(hh.time,utc=True); hh=hh.set_index('t').close
dl=hh.resample('1D').last().dropna(); lrd=np.log(dl).diff().dropna()
print('\n[実運用期間] 11/8〜11/24 の日次騰落(UTC日足) 標準偏差 %.4f / 平均 %.4f / 予想側のEWMAσ(日次) 平均 %.4f'%(lrd['2026-11-08':'2026-11-24'].std(),lrd['2026-11-08':'2026-11-24'].mean(),S.vol.mean()))
print(lrd['2026-11-01':'2026-11-24'].round(3).to_string())
