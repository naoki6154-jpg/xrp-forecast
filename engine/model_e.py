"""段階4：モデルE（機械学習）と、相場の状態の統計的な推定（隠れマルコフモデル）。設計書 第4章・第5章。

モデルE：日足の特徴量から「期限に今より上がっているか」を勾配ブースティングで直接学習する。
  過学習を避けるため、前進型の検証（その年より前のデータだけで学び、その年で試す、を2021年から繰り返す）で
  ブライアスコアが基準（いつも同じ確率）を安定して上回るまで、予想の重みは0（参考表示だけ）。
相場の状態：日々の値動き（騰落と大きさ）から、観測できない3つの状態を確率で推定する（自作の隠れマルコフモデル。乱数は使わない）。
"""
import math
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

HS = {"1d": 1, "1w": 7, "1m": 30}
FEATS = [("r1", "1日の騰落"), ("r7", "7日の騰落"), ("r30", "30日の騰落"), ("dev50", "50日線からの乖離"), ("dev200", "200日線からの乖離"),
         ("vol30", "30日の変動率"), ("vol_ratio", "変動率の変化（7日/30日）"), ("fg", "恐怖強欲指数"), ("btc_r7", "BTCの7日の騰落"),
         ("xrp_btc_r30", "XRPのBTCに対する30日の強さ"), ("dow", "曜日")]


def features(close, fg, btc):
    lr = np.log(close).diff()
    df = pd.DataFrame(index=close.index)
    df["r1"] = lr
    df["r7"] = np.log(close / close.shift(7))
    df["r30"] = np.log(close / close.shift(30))
    df["dev50"] = np.log(close / close.rolling(50).mean())
    df["dev200"] = np.log(close / close.rolling(200).mean())
    df["vol30"] = lr.rolling(30).std()
    df["vol_ratio"] = lr.rolling(7).std() / df["vol30"]
    df["fg"] = fg.reindex(df.index).ffill()
    b = btc.reindex(df.index).ffill()
    df["btc_r7"] = np.log(b / b.shift(7))
    df["xrp_btc_r30"] = df["r30"] - np.log(b / b.shift(30))
    df["dow"] = df.index.dayofweek
    return df


def _model():
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=40, l2_regularization=1.0, random_state=0)


def run(close, fg, btc, now_date):
    X = features(close, fg, btc)
    cols = [c for c, _ in FEATS]
    out = {"features": [{"name": nm, "now": (None if not np.isfinite(X[c].iloc[-1]) else float(X[c].iloc[-1]))} for c, nm in FEATS], "per_h": {}}
    lc = np.log(close)
    for hid, h in HS.items():
        y = (lc.shift(-h) - lc > 0).astype(float)
        y[lc.shift(-h).isna()] = np.nan
        d = X.assign(y=y).dropna(subset=cols[:-1])
        tr = d.dropna(subset=["y"])
        # 前進型の検証：各年を、その年の初めより h 日以上前のデータだけで学んだモデルで試す
        wf = []
        for yr in range(2021, now_date.year + 1):
            t0 = pd.Timestamp(year=yr, month=1, day=1)
            a = tr[tr.index < t0 - pd.Timedelta(days=h)]
            b = tr[(tr.index >= t0) & (tr.index < pd.Timestamp(year=yr + 1, month=1, day=1))]
            if len(a) < 500 or len(b) < 30:
                continue
            m = _model().fit(a[cols], a.y)
            p = m.predict_proba(b[cols])[:, 1]
            base = float(a.y.mean())
            wf.append({"year": yr, "n": int(len(b)), "brier": float(np.mean((p - b.y) ** 2)), "brier_base": float(np.mean((base - b.y) ** 2)),
                       "hit": float(np.mean((p >= 0.5) == (b.y == 1)))})
        m = _model().fit(tr[cols], tr.y)
        pnow = float(m.predict_proba(d[cols].iloc[[-1]])[:, 1][0])
        nb = sum(w["n"] for w in wf)
        br = sum(w["brier"] * w["n"] for w in wf) / nb if nb else None
        bb = sum(w["brier_base"] * w["n"] for w in wf) / nb if nb else None
        wins = sum(1 for w in wf if w["brier"] < w["brier_base"])
        passed = bool(wf) and br < bb - 0.002 and wins >= math.ceil(len(wf) * 0.8)
        out["per_h"][hid] = {"p_up": pnow, "walk_forward": wf, "brier": br, "brier_base": bb, "years_better": wins, "years": len(wf),
                             "passed": passed, "n_train": int(len(tr))}
    out["rule"] = "前進型の検証で、ブライアスコアが基準より0.002以上良く、8割以上の年で基準を上回ったら「合格」。合格しても重みを入れるのは🔬検証・改良チャットの判断（設計書 第13章）"
    return out


# ---------- 隠れマルコフモデル（3状態、正規分布、Baum-Welch） ----------
def _hmm_fit(x, K=3, iters=60):
    n = len(x)
    qs = np.quantile(np.abs(x), [0.3, 0.7, 0.95])
    mu = np.zeros(K); sd = np.array(sorted([qs[0], qs[1], qs[2]])) + 1e-4
    A = np.full((K, K), 0.02 / (K - 1)); np.fill_diagonal(A, 0.98)
    pi = np.full(K, 1 / K)
    for _ in range(iters):
        B = np.exp(-0.5 * ((x[:, None] - mu) / sd) ** 2) / (sd * math.sqrt(2 * math.pi)) + 1e-300
        al = np.zeros((n, K)); c = np.zeros(n)
        al[0] = pi * B[0]; c[0] = al[0].sum(); al[0] /= c[0]
        for t in range(1, n):
            al[t] = (al[t - 1] @ A) * B[t]; c[t] = al[t].sum(); al[t] /= c[t]
        be = np.ones((n, K))
        for t in range(n - 2, -1, -1):
            be[t] = (A @ (B[t + 1] * be[t + 1])) / c[t + 1]
        g = al * be; g /= g.sum(1, keepdims=True)
        xi = (al[:-1, :, None] * A[None] * (B[1:] * be[1:])[:, None, :]) / c[1:, None, None]
        A = xi.sum(0) / xi.sum((0, 2))[:, None]; A /= A.sum(1, keepdims=True)
        pi = g[0]
        w = g.sum(0)
        mu = (g * x[:, None]).sum(0) / w
        sd = np.sqrt((g * (x[:, None] - mu) ** 2).sum(0) / w) + 1e-5
    return mu, sd, A, al, g


def regime_hmm(close, since="2018-06-01"):
    x = np.log(close[close.index >= since]).diff().dropna()
    mu, sd, A, filt, smooth = _hmm_fit(x.values)
    order = np.argsort(sd)
    names = ["落ち着いた相場", "ふつうの相場", "荒れた相場"]
    st = []
    for rank, k in enumerate(order):
        st.append({"name": names[rank], "mean_daily_pct": float(mu[k] * 100), "vol_annual_pct": float(sd[k] * math.sqrt(365) * 100),
                   "stay_days": float(1 / max(1 - A[k, k], 1e-6)), "p_now": float(filt[-1, k]), "share_hist": float(smooth[:, k].mean())})
    cur = max(st, key=lambda s: s["p_now"])
    path = [names[list(order).index(int(np.argmax(filt[i])))] for i in range(len(x))]
    since_d = None
    for i in range(len(path) - 1, -1, -1):
        if path[i] != cur["name"]:
            since_d = str(x.index[i + 1].date()) if i + 1 < len(x) else None
            break
    prev = path[-2] if len(path) > 1 else None
    return {"states": st, "now": cur["name"], "p_now": cur["p_now"], "since": since_d, "prev_day": prev,
            "last30": [[str(x.index[i].date()), path[i]] for i in range(max(0, len(x) - 30), len(x))],
            "note": "日々の騰落率から3つの状態を推定（隠れマルコフモデル、2018年6月〜）。状態の名前は変動の大きさの順。今日の確率は今日までの情報だけで計算（後から見直さない値）"}
