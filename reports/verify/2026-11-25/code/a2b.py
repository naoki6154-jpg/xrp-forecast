import sys; sys.path.insert(0,"/home/claude/work")
import pandas as pd, numpy as np
df=pd.read_pickle('df.pkl'); T0=pd.Timestamp('2026-11-08 06:00',tz='Asia/Tokyo')
S=df[df['mode'].isin(['morning','night'])&(df['at']>=T0)&df.pxT.notna()].copy(); S['up']=(S.pxT>S.s0).astype(float)
def auc(p,y):
    p=np.asarray(p);y=np.asarray(y); pos=p[y==1]; neg=p[y==0]
    if len(pos)==0 or len(neg)==0: return np.nan
    return ((pos[:,None]>neg[None,:]).sum()+0.5*(pos[:,None]==neg[None,:]).sum())/(len(pos)*len(neg))
for h in('1d','1w'):
    x=S[S.h==h]; print(h,'上がった件',int(x.up.sum()),'/',len(x))
    for m,c in [('A','p_A'),('B','p_B'),('C','p_C'),('D','p_D'),('T','p_T'),('E','pE'),('P','pP'),('混合(補正前)','p'),('表示(補正後)','pj')]:
        y=x[x[c].notna()]; cen=((y[c]-y[c].mean()+y.up.mean()-y.up)**2).mean(); base=y.up.var(ddof=0)
        print('  %-10s AUC %.3f  水準を揃えたブライア %.4f (一定の予想=%.4f)  差 %+.4f  相関 %+.2f'%(m,auc(y[c],y.up),cen,base,cen-base,np.corrcoef(y[c],y.up)[0,1]))
print('--- 版1.4.0のみ(11/16〜) 1日後')
x=S[(S.h=='1d')&(S.ver=='1.4.0')]; print(len(x),'上昇率',x.up.mean())
for m,c in [('A','p_A'),('B','p_B'),('C','p_C'),('D','p_D'),('T','p_T'),('E','pE'),('P','pP'),('混合','p'),('表示','pj')]:
    y=x[x[c].notna()]; print('  %-6s pmean %.3f Brier %.4f AUC %.3f'%(m,y[c].mean(),((y[c]-y.up)**2).mean(),auc(y[c],y.up)))
