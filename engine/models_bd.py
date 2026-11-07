"""モデルB（似た過去の局面）とモデルD（季節性・4年サイクル）。設計書 第5章。
どちらも「過去に実際に起きた値動き」を、今の変動の大きさに合わせて今の価格に当てはめる。
答えの形はモデルA・Cと同じ（期限ごとの価格の標本）。乱数は seed から作るので、同じ入力なら同じ結果になる。
"""
import math
import numpy as np
import pandas as pd


def daily_frame(close_usd, fear_greed):
    """日足から特徴量を作る（その日までに知り得た値だけで計算する）。"""
    s = close_usd.astype(float)
    lr = np.log(s).diff()
    df = pd.DataFrame({"close": s})
    df["r7"] = np.log(s / s.shift(7))
    df["r30"] = np.log(s / s.shift(30))
    df["dev200"] = np.log(s / s.rolling(200).mean())
    df["vol30"] = lr.rolling(30).std()
    fg = fear_greed.reindex(df.index).ffill()
    df["fg"] = fg
    return df


FEATURES = [("r7", "7日の騰落率"), ("r30", "30日の騰落率"), ("dev200", "200日線からの乖離"), ("vol30", "30日の変動率"), ("fg", "恐怖強欲指数")]


def model_b(df, steps, n, seed, sig_now, k=60, gap=5, exclude_recent=60, jitter=0.3):
    """今と特徴量が近い過去の日を k 日選び（互いに gap 日以上離す）、その後 steps 日の値動きを標本にする。
    値動きは、その日の30日変動率と今の変動率の比で大きさを合わせる。"""
    cols = [c for c, _ in FEATURES]
    hist = df.dropna(subset=cols)
    now = hist.iloc[-1]
    mu = hist[cols].mean(); sd = hist[cols].std()
    z = (hist[cols] - mu) / sd
    znow = (now[cols] - mu) / sd
    dist = np.sqrt(((z - znow) ** 2).sum(axis=1))
    last = len(df) - 1
    pos = {d: i for i, d in enumerate(df.index)}
    rng = np.random.default_rng(seed)
    logc = np.log(df["close"].values)
    out, info = [], []
    for h in steps:
        cand = [(dist[d], pos[d]) for d in dist.index if pos[d] + h <= last and pos[d] <= last - exclude_recent]
        cand.sort()
        pick = []
        for dd, i in cand:
            if all(abs(i - j) >= gap for _, j in pick):
                pick.append((dd, i))
            if len(pick) >= k:
                break
        idx = np.array([i for _, i in pick])
        fwd = logc[idx + h] - logc[idx]
        scale = sig_now / df["vol30"].values[idx]
        r = fwd * scale
        draw = rng.integers(0, len(r), n)
        samp = r[draw] + rng.normal(0, jitter * sig_now * math.sqrt(h), n)
        out.append(samp.astype(np.float32))
        info.append({"h": h, "k": len(idx), "median_dist": float(np.median([d for d, _ in pick])) if pick else None,
                     "p_up_raw": float((fwd > 0).mean()) if len(fwd) else None,
                     "dates": [str(df.index[i].date()) for i in idx[:8]]})
    return np.stack(out, axis=1), info, {c: float(now[c]) for c in cols}


def model_d(df, now_date, steps, n, seed, sig_now, cycle_years=(2), jitter=0.3):
    """過去の各年の「同じ月日から steps 日後まで」の値動きを標本にする。今年と4年サイクルの位置が同じ年（年を4で割った余りが同じ）は重み2倍。"""
    rng = np.random.default_rng(seed)
    logc = np.log(df["close"])
    years = sorted({d.year for d in df.index})
    out, info = [], []
    for h in steps:
        rs, ws, used = [], [], []
        for y in years:
            if y >= now_date.year:
                continue
            try:
                t0 = pd.Timestamp(year=y, month=now_date.month, day=now_date.day)
            except ValueError:
                t0 = pd.Timestamp(year=y, month=now_date.month, day=28)
            t1 = t0 + pd.Timedelta(days=h)
            a = logc[logc.index <= t0]; b = logc[logc.index <= t1]
            if not len(a) or not len(b) or (t0 - a.index[-1]).days > 3 or (t1 - b.index[-1]).days > 3 or t1 > df.index[-1]:
                continue
            v = df["vol30"].asof(t0)
            if not v or not np.isfinite(v):
                continue
            r = (b.iloc[-1] - a.iloc[-1]) * sig_now / v
            w = 2.0 if (y % 4) == (now_date.year % 4) else 1.0
            rs.append(r); ws.append(w); used.append((y, round(float(b.iloc[-1] - a.iloc[-1]), 4), w))
        rs = np.array(rs); ws = np.array(ws) / np.sum(ws)
        draw = rng.choice(len(rs), n, p=ws)
        samp = rs[draw] + rng.normal(0, jitter * sig_now * math.sqrt(h), n)
        out.append(samp.astype(np.float32))
        info.append({"h": h, "years": [u[0] for u in used], "raw_returns": [u[1] for u in used], "weights": [u[2] for u in used],
                     "p_up_raw_weighted": float(np.sum(ws * (rs > 0))) if len(rs) else None})
    return np.stack(out, axis=1), info


def iv_term(path, now_utc, max_age_days=2):
    """Deribit の満期ごとの予想変動率（年率%）から、合計の分散の曲線を作る。古い・無いときは None。"""
    try:
        d = pd.read_csv(path)
    except Exception:
        return None
    if not len(d):
        return None
    snap = d.snapshot_date.max()
    d = d[d.snapshot_date == snap]
    fetched = pd.to_datetime(d.fetched_at.max(), utc=True)
    if (now_utc - fetched.to_pydatetime()).total_seconds() > max_age_days * 86400:
        return None
    d = d[d.days_to_expiry >= 1].sort_values("days_to_expiry")
    if len(d) < 2:
        return None
    t = np.r_[0.0, d.days_to_expiry.values.astype(float)]
    var = np.r_[0.0, (d.atm_mark_iv_pct.values.astype(float) / 100) ** 2 * d.days_to_expiry.values / 365]
    return {"t": t, "var": var, "snapshot": snap, "fetched_at": str(d.fetched_at.max()),
            "points": [[float(a), float(b)] for a, b in zip(d.days_to_expiry, d.atm_mark_iv_pct)]}


def iv_step_sigmas(term, T):
    """1日ごとの変動（標準偏差）。満期の間は合計の分散を直線でつなぐ。最後の満期より先は最後の値のまま。"""
    t, v = term["t"], term["var"]
    days = np.arange(0, T + 1, dtype=float)
    last_rate = (v[-1] - v[-2]) / (t[-1] - t[-2])
    V = np.where(days <= t[-1], np.interp(days, t, v), v[-1] + (days - t[-1]) * last_rate)
    dv = np.maximum(np.diff(V), 1e-8)
    return np.sqrt(dv).astype(np.float32)


def iv_at(term, days):
    t, v = term["t"], term["var"]
    V = float(np.interp(days, t, v)) if days <= t[-1] else float(v[-1] + (days - t[-1]) * (v[-1] - v[-2]) / (t[-1] - t[-2]))
    return math.sqrt(V / days * 365)
