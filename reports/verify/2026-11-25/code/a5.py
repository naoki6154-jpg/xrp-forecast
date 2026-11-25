import sys; sys.path.insert(0,"/home/claude/work")
import pandas as pd, numpy as np, math
pd.set_option('display.width',230); pd.set_option('display.float_format','{:.4f}'.format); pd.set_option('display.max_columns',40)
rng=np.random.default_rng(5)
df=pd.read_pickle('df.pkl'); T0=pd.Timestamp('2026-11-08 06:00',tz='Asia/Tokyo')
S=df[df['mode'].isin(['morning','night'])&(df['at']>=T0)&df.pxT.notna()].copy(); S['up']=(S.pxT>S.s0).astype(float)
# --- 直前の傾向を使う単純な基準（予想時点で分かる情報のみ）
d1=pd.read_csv('/home/claude/xrp-forecast/data/raw/prices/binance_xrp_usdt_1d.csv'); d1['date']=pd.to_datetime(d1.date); c=d1.set_index('date').close
r1=np.log(c).diff(); r7=np.log(c).diff(7)
def trail1(t): 
    d=pd.Timestamp(t.tz_convert('UTC').date())-pd.Timedelta(days=1); x=r1[:d].tail(30); return (x>0).mean()
def trail7(t):
    d=pd.Timestamp(t.tz_convert('UTC').date())-pd.Timedelta(days=1); x=r7[:d].tail(60); return (x>0).mean()
S['pbase']=[trail1(t) if h=='1d' else trail7(t) for t,h in zip(S['at'],S.h)]
print('== 直前の傾向を使う基準（過去30日の上昇日の割合／過去60日の7日騰落が上昇だった割合）')
for h in('1d','1w'):
    x=S[S.h==h]; print(h,'n',len(x),'基準p平均 %.3f 実際 %.3f | Brier: 基準(直前傾向) %.4f / 常に50%% 0.2500 / 補正前 %.4f / 表示(補正後,円×ドル結果) %.4f'%(x.pbase.mean(),x.up.mean(),((x.pbase-x.up)**2).mean(),((x.p-x.up)**2).mean(),((x.pj-x.up)**2).mean()))
# --- 実質の件数
print('\n== 実質の件数（実運用）')
for h in('1d','1w'):
    x=S[S.h==h]; days=x.target.dt.date.nunique(); span=(x.target.max()-x.target.min()).total_seconds()/86400; 
    print(h,'採点件数',len(x),'目標日の数',days,'目標の期間(日)%.1f'%span,'作成の期間(日)%.1f'%((x['at'].max()-x['at'].min()).total_seconds()/86400),'→ 独立に近い単位 %s'%(days if h=='1d' else '約%.1f週(=作成期間%.1f日/7)'%((x['at'].max()-x['at'].min()).total_seconds()/86400/7+1e-9,(x['at'].max()-x['at'].min()).total_seconds()/86400)))
# 日ごとのまとまりで補正した標準誤差と、素朴な標準誤差の比（設計効果）
for h,key in(('1d','target'),):
    x=S[S.h==h].copy(); x['d']=(x.p-x.up)**2-0.25; x['day']=x.target.dt.date
    naive=x.d.std()/math.sqrt(len(x)); g=x.groupby('day').d.mean(); clus=g.std()/math.sqrt(len(g))
    print('1日後 Brier-0.25: 平均 %.4f  素朴SE %.4f  日ごとにまとめたSE %.4f (比 %.2f)'%(x.d.mean(),naive,clus,clus/naive))
# --- 履歴(前進型)での差の標準偏差から、必要な期間を計算
w=pd.read_pickle('wf.pkl').rename(columns={'sig_x':'sig'})
cal={'1d':(-0.1604416102678666,-0.20724879965232088),'1w':(-0.062206898959351664,0.6416540460568133)}
def lg(p): p=np.clip(p,0.01,0.99); return np.log(p/(1-p))
def sd_blocks(d,blk,B=800):
    d=np.asarray(d,float); n=len(d); out=[]
    for _ in range(B):
        s=rng.integers(0,n,size=int(np.ceil(n/blk))); idx=np.concatenate([np.arange(a,a+blk)%n for a in s])[:n]; out.append(d[idx].mean())
    return np.std(out)
print('\n== 採用基準（基準より0.002以上良い）を判定するのに必要な期間（過去2020〜の前進型の差のばらつきから）')
rows=[]
for h,blk in(('1d',7),('1w',21)):
    for m,lab in(('E','混合E(ML参考)'),('C','C単独'),('B','B'),('T','T')):
        g=w[(w.h==h)&(w.m==m)].sort_values('t'); d=((g.p_up-g.up)**2-0.25).values
        N=len(d); se=sd_blocks(d,blk)   # 履歴全体(N日分)での平均差の標準誤差
        for delta in(0.002,0.005,0.01):
            # 目標: 差deltaを、検出力80%・片側5%で判定 → 必要日数 = N*((1.645+0.84)*se/delta)^2
            need_days=N*((1.645+0.84)*se/delta)**2
            rows.append(dict(h=h,model=lab,delta=delta,履歴SE=se,必要日数=need_days,必要年数=need_days/365))
r=pd.DataFrame(rows); print(r.pivot_table(index=['h','model'],columns='delta',values='必要年数').to_string())
# 補正後(表示値に近いもの)も
for h,blk in(('1d',7),('1w',21)):
    g=w[(w.h==h)&(w.m=='E')].sort_values('t'); a,b=cal[h]; pc=1/(1+np.exp(-(a+b*lg(g.p_up.values)))); d=((pc-g.up.values)**2-0.25); N=len(d); se=sd_blocks(d,blk)
    print(h,'補正後E: 履歴の平均差 %.4f SE %.4f → 0.002を判定するのに %.0f年'%(d.mean(),se,N*((1.645+.84)*se/0.002)**2/365))
# 範囲の的中率：80%範囲が80%かを±10ptで判定するのに必要な独立件数
print('\n== 範囲の的中率の判定（二項分布の標準誤差）: 80%範囲が本当に80%か、±10ポイントの差を判定するのに必要な独立な件数')
for tol in(0.10,0.05):
    n=(1.96*math.sqrt(.8*.2)/tol)**2; print(' 許容±%.0fポイント: 独立な件数 %.0f件 → 1日後なら約%.0f日、1週間後なら約%.0f週(約%.1f年)'%(tol*100,n,n,n,n/52))
