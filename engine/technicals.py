"""テクニカル指標と、そのパターンからのシミュレーション（モデルT）。2026-11-08 追加。

指標（日足・Binance XRP/USDT、2018年5月〜）：
  移動平均（MA20・50・200）、指数移動平均（EMA12・26・50・200）、MACD（12・26・9）、RSI（14）、
  ボリンジャーバンド（20日・±2σ）、パラボリックSAR（0.02・最大0.2）、出来高の移動平均（MAVOL20）、
  ストキャスティクス（14・3）、ADX（14、トレンドの強さ）、ATR（14、値幅）、OBV（出来高の流れ）、
  一目均衡表の雲（9・26・52）、恐怖強欲指数（暗号資産）、米国株（ナスダック）とドル指数の20日の向き、
  VIX（米国株の恐怖指数。データがあれば）。
各指標の「今の状態」を、決まった規則で区分（例：RSI 30未満＝売られすぎ）し、強気＋1／中立0／弱気−1 の印を付ける（一般的な読み方。予想の数字にはしない）。
統計：同じ状態だった過去の日の、その後7日・30日の上昇の割合と値動きの中央値（件数つき）。
モデルT：今と「指標の状態の組み合わせ」が最も多く一致する過去の日を選び、その後の値動きを今の変動の大きさに合わせて使う（モデルBの、テクニカル版）。
"""
import math
import numpy as np
import pandas as pd


def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _rma(s, n):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def psar(high, low, step=0.02, mx=0.2):
    h, l = high.values, low.values
    n = len(h)
    sar = np.zeros(n); trend = np.ones(n)
    up = True; af = step; ep = h[0]; sar[0] = l[0]
    for i in range(1, n):
        s = sar[i - 1] + af * (ep - sar[i - 1])
        if up:
            s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s:
                up = False; s = ep; ep = l[i]; af = step
            elif h[i] > ep:
                ep = h[i]; af = min(af + step, mx)
        else:
            s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s:
                up = True; s = ep; ep = h[i]; af = step
            elif l[i] < ep:
                ep = l[i]; af = min(af + step, mx)
        sar[i] = s; trend[i] = 1 if up else -1
    return pd.Series(sar, index=high.index), pd.Series(trend, index=high.index)


def compute(ohlcv, fg=None, nasdaq=None, dxy=None, vix=None):
    o = ohlcv.astype(float)
    c, h, l, v = o["close"], o["high"], o["low"], o["volume"]
    X = pd.DataFrame(index=o.index)
    X["close"] = c
    for n in (20, 50, 200):
        X["ma%d" % n] = c.rolling(n).mean()
    for n in (12, 26, 50, 200):
        X["ema%d" % n] = _ema(c, n)
    X["macd"] = X.ema12 - X.ema26
    X["macd_sig"] = _ema(X.macd, 9)
    X["macd_hist"] = X.macd - X.macd_sig
    d = c.diff()
    X["rsi"] = 100 - 100 / (1 + _rma(d.clip(lower=0), 14) / _rma((-d).clip(lower=0), 14))
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std()
    X["bb_up"], X["bb_lo"] = m20 + 2 * s20, m20 - 2 * s20
    X["bb_pctb"] = (c - X.bb_lo) / (X.bb_up - X.bb_lo)
    X["bb_width"] = (X.bb_up - X.bb_lo) / m20
    X["bb_width_pct"] = X.bb_width.rolling(365, min_periods=120).rank(pct=True)
    X["sar"], X["sar_trend"] = psar(h, l)
    X["mavol20"] = v.rolling(20).mean()
    X["vol_ratio"] = v / X.mavol20
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    X["stoch_k"] = 100 * (c - ll) / (hh - ll)
    X["stoch_d"] = X.stoch_k.rolling(3).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = _rma(tr, 14)
    X["atr_pct"] = atr / c
    upm, dnm = h.diff(), -l.diff()
    pdm = pd.Series(np.where((upm > dnm) & (upm > 0), upm, 0.0), index=c.index)
    ndm = pd.Series(np.where((dnm > upm) & (dnm > 0), dnm, 0.0), index=c.index)
    pdi, ndi = 100 * _rma(pdm, 14) / atr, 100 * _rma(ndm, 14) / atr
    X["adx"] = _rma(100 * (pdi - ndi).abs() / (pdi + ndi), 14)
    X["di_diff"] = pdi - ndi
    obv = (np.sign(d).fillna(0) * v).cumsum()
    X["obv_slope"] = (obv - obv.shift(20)) / v.rolling(20).mean() / 20
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    spa = ((tenkan + kijun) / 2).shift(26)
    spb = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    X["cloud_top"], X["cloud_bot"] = np.maximum(spa, spb), np.minimum(spa, spb)
    X["tenkan"], X["kijun"] = tenkan, kijun
    if fg is not None:
        X["fg"] = fg.reindex(X.index).ffill()
    for nm, s in (("nasdaq", nasdaq), ("dxy", dxy), ("vix", vix)):
        if s is not None and len(s):
            ss = s.reindex(X.index).ffill()
            X[nm] = ss
            X[nm + "_r20"] = np.log(ss / ss.shift(20))
    return X


def _cut(x, bins, labels):
    if x is None or not np.isfinite(x):
        return None
    for b, lab in zip(bins, labels):
        if x < b:
            return lab
    return labels[-1]


# 指標ごとの「状態」の決め方：(名前, 列, 区分の関数, 状態→印, 読み方)
def _rules():
    R = []
    R.append(("移動平均（MA20・50・200）の並び", "ma_align",
              lambda r: ("上昇の並び" if r.close > r.ma20 > r.ma50 > r.ma200 else "下降の並び" if r.close < r.ma20 < r.ma50 < r.ma200 else "混在"),
              {"上昇の並び": 1, "混在": 0, "下降の並び": -1}, "価格＞MA20＞MA50＞MA200 なら上昇の並び（強気）、逆なら下降の並び（弱気）"))
    R.append(("200日線との位置", "ma200_pos", lambda r: ("上" if r.close > r.ma200 else "下"), {"上": 1, "下": -1}, "長期の流れ。上なら強気"))
    R.append(("EMA12とEMA26", "ema_cross", lambda r: ("EMA12が上" if r.ema12 > r.ema26 else "EMA12が下"), {"EMA12が上": 1, "EMA12が下": -1}, "短期の流れ"))
    R.append(("EMA50とEMA200（ゴールデン・デッドクロス）", "ema_long", lambda r: ("ゴールデン（50が上）" if r.ema50 > r.ema200 else "デッド（50が下）"),
              {"ゴールデン（50が上）": 1, "デッド（50が下）": -1}, "中長期の流れ"))
    R.append(("MACD", "macd_state", lambda r: ("0より上・シグナルより上" if r.macd > 0 and r.macd_hist > 0 else "0より上・シグナルより下" if r.macd > 0 else
                                                 "0より下・シグナルより上" if r.macd_hist > 0 else "0より下・シグナルより下"),
              {"0より上・シグナルより上": 1, "0より上・シグナルより下": 0, "0より下・シグナルより上": 0, "0より下・シグナルより下": -1}, "勢い。0とシグナル線の両方の上なら強気"))
    R.append(("RSI（14）", "rsi_zone", lambda r: _cut(r.rsi, [30, 45, 55, 70], ["30未満（売られすぎ）", "30〜45（弱い）", "45〜55（中立）", "55〜70（強い）", "70以上（買われすぎ）"]),
              {"30未満（売られすぎ）": 1, "30〜45（弱い）": -1, "45〜55（中立）": 0, "55〜70（強い）": 1, "70以上（買われすぎ）": -1},
              "70以上は買われすぎ（反落しやすい）、30未満は売られすぎ（反発しやすい）とされる"))
    R.append(("ボリンジャーバンド（位置）", "bb_zone", lambda r: _cut(r.bb_pctb, [0, 0.2, 0.8, 1.0], ["下のバンドの外", "下のバンド付近", "バンドの中", "上のバンド付近", "上のバンドの外"]),
              {"下のバンドの外": 1, "下のバンド付近": 1, "バンドの中": 0, "上のバンド付近": -1, "上のバンドの外": -1}, "バンドの外は行き過ぎ（戻りやすい）とされる"))
    R.append(("ボリンジャーバンド（幅）", "bb_squeeze", lambda r: _cut(r.bb_width_pct, [0.2, 0.8], ["狭い（スクイーズ）", "ふつう", "広い"]),
              {"狭い（スクイーズ）": 0, "ふつう": 0, "広い": 0}, "幅が過去1年で狭いと、その後に大きく動きやすい（向きは分からない）"))
    R.append(("パラボリックSAR", "sar_dir", lambda r: ("上昇（SARが下）" if r.sar_trend > 0 else "下降（SARが上）"), {"上昇（SARが下）": 1, "下降（SARが上）": -1}, "流れの向き"))
    R.append(("出来高（MAVOL20との比）", "vol_zone", lambda r: _cut(r.vol_ratio, [0.7, 1.5], ["少ない（0.7倍未満）", "ふつう", "多い（1.5倍以上）"]),
              {"少ない（0.7倍未満）": 0, "ふつう": 0, "多い（1.5倍以上）": 0}, "多い日は動きが本物になりやすい（向きは価格と合わせて見る）"))
    R.append(("ストキャスティクス（14・3）", "stoch_zone", lambda r: _cut(r.stoch_k, [20, 80], ["20未満（売られすぎ）", "20〜80", "80以上（買われすぎ）"]),
              {"20未満（売られすぎ）": 1, "20〜80": 0, "80以上（買われすぎ）": -1}, "RSIと同じく行き過ぎを見る"))
    R.append(("ADX（トレンドの強さ）", "adx_zone", lambda r: ("強いトレンド・上向き" if r.adx >= 25 and r.di_diff > 0 else "強いトレンド・下向き" if r.adx >= 25 else "トレンドなし（25未満）"),
              {"強いトレンド・上向き": 1, "強いトレンド・下向き": -1, "トレンドなし（25未満）": 0}, "25以上でトレンドあり。向きは＋DIと−DIの差"))
    R.append(("OBV（出来高の流れ、20日）", "obv_dir", lambda r: ("増加" if r.obv_slope > 0.05 else "減少" if r.obv_slope < -0.05 else "横ばい"),
              {"増加": 1, "横ばい": 0, "減少": -1}, "買いの出来高が多いと増える"))
    R.append(("一目均衡表（雲）", "cloud_pos", lambda r: ("雲の上" if r.close > r.cloud_top else "雲の下" if r.close < r.cloud_bot else "雲の中"),
              {"雲の上": 1, "雲の中": 0, "雲の下": -1}, "雲の上は強気、下は弱気"))
    R.append(("恐怖強欲指数（暗号資産）", "fg_zone", lambda r: _cut(getattr(r, "fg", np.nan), [25, 45, 55, 75], ["25未満（極度の恐怖）", "25〜45（恐怖）", "45〜55（中立）", "55〜75（強欲）", "75以上（極度の強欲）"]),
              {"25未満（極度の恐怖）": 1, "25〜45（恐怖）": 0, "45〜55（中立）": 0, "55〜75（強欲）": 0, "75以上（極度の強欲）": -1}, "極度の恐怖は逆張りの買い場、極度の強欲は過熱とされる"))
    R.append(("米国株（ナスダック）の20日", "nasdaq_dir", lambda r: _cut(getattr(r, "nasdaq_r20", np.nan), [-0.03, 0.03], ["下げ（−3%以下）", "横ばい", "上げ（＋3%以上）"]),
              {"下げ（−3%以下）": -1, "横ばい": 0, "上げ（＋3%以上）": 1}, "暗号資産は米国株と連動しやすい"))
    R.append(("ドル指数の20日", "dxy_dir", lambda r: _cut(getattr(r, "dxy_r20", np.nan), [-0.01, 0.01], ["ドル安（−1%以下）", "横ばい", "ドル高（＋1%以上）"]),
              {"ドル安（−1%以下）": 1, "横ばい": 0, "ドル高（＋1%以上）": -1}, "ドル高は暗号資産に逆風とされる"))
    R.append(("VIX（米国株の恐怖指数）", "vix_zone", lambda r: _cut(getattr(r, "vix", np.nan), [15, 25, 35], ["15未満（楽観）", "15〜25", "25〜35（不安）", "35以上（パニック）"]),
              {"15未満（楽観）": 0, "15〜25": 0, "25〜35（不安）": -1, "35以上（パニック）": 1}, "不安の高まりは逆風、パニックは底のサインとされる"))
    return R


RULES = _rules()


def states(X):
    """日ごとの状態の表（列＝指標、値＝状態の名前。計算できない日は None）。"""
    rows = {}
    for nm, col, f, _, _ in RULES:
        vals = []
        for r in X.itertuples():
            try:
                vals.append(f(r))
            except Exception:
                vals.append(None)
        rows[col] = vals
    S = pd.DataFrame(rows, index=X.index)
    S = S.where(S.notna(), None)
    for nm, col, f, _, _ in RULES:          # 計算に必要な値が欠けている日は None（比較で常に偽になるため）
        pass
    return S


def analyze(X, close, hs=(7, 30), start="2018-12-01"):
    S = states(X)
    lc = np.log(close)
    fwd = {h: (lc.shift(-h) - lc) for h in hs}
    now = S.iloc[-1]
    items = []
    score = 0; nscore = 0
    for nm, col, f, sig, how in RULES:
        st = now[col]
        valid = X.index >= start
        r = {"name": nm, "id": col, "state": st, "signal": sig.get(st) if st else None, "how": how, "stats": {}}
        if st is not None:
            m = (S[col] == st) & valid
            for h in hs:
                x = fwd[h][m].dropna()
                base = fwd[h][valid].dropna()
                r["stats"][str(h)] = {"n": int(len(x)), "up": float((x > 0).mean()) if len(x) else None, "median": float(x.median()) if len(x) else None,
                                      "base_up": float((base > 0).mean())}
            if r["signal"] is not None:
                score += r["signal"]; nscore += 1
        items.append(r)
    # 合計の印（強気の数−弱気の数）の、過去の同じ水準の成績
    sigdf = pd.DataFrame({col: S[col].map(lambda s, sg=sig: sg.get(s) if s else np.nan) for _, col, _, sig, _ in RULES})
    tot = sigdf.sum(axis=1, min_count=8)
    cur = tot.iloc[-1]
    band = (tot >= cur - 1) & (tot <= cur + 1) & (X.index >= start)
    comp = {"score": float(cur) if np.isfinite(cur) else None, "n_signals": nscore, "stats": {}}
    for h in hs:
        x = fwd[h][band].dropna()
        comp["stats"][str(h)] = {"n": int(len(x)), "up": float((x > 0).mean()) if len(x) else None, "median": float(x.median()) if len(x) else None}
    vals = {k: (None if not np.isfinite(X[k].iloc[-1]) else float(X[k].iloc[-1])) for k in
            ["close", "ma20", "ma50", "ma200", "ema12", "ema26", "ema50", "ema200", "macd", "macd_sig", "macd_hist", "rsi", "bb_up", "bb_lo", "bb_pctb", "bb_width",
             "sar", "mavol20", "vol_ratio", "stoch_k", "stoch_d", "adx", "di_diff", "atr_pct", "obv_slope", "cloud_top", "cloud_bot", "fg", "nasdaq_r20", "dxy_r20", "vix"] if k in X}
    return {"items": items, "composite": comp, "values": vals, "date": str(X.index[-1].date()), "states": S}


def model_t(X, S, close, steps, n, seed, sig_now, k=80, gap=3, exclude_recent=30, jitter=0.3, start="2018-12-01"):
    """モデルT：指標の状態の組み合わせが今と最も多く一致する過去の日を k 日選び、その後の値動きを標本にする。"""
    cols = [c for _, c, _, _, _ in RULES]
    now = S.iloc[-1]
    use = [c for c in cols if now[c] is not None]
    hist = S[(S.index >= start)]
    match = sum((hist[c] == now[c]).astype(int) for c in use)
    lr = np.log(close).diff()
    vol30 = lr.rolling(30).std()
    logc = np.log(close.values)
    pos = {d: i for i, d in enumerate(close.index)}
    last = len(close) - 1
    rng = np.random.default_rng(seed)
    out, info = [], []
    order = match.sort_values(ascending=False, kind="stable")
    for h in steps:
        pick = []
        for d, sc in order.items():
            i = pos[d]
            if i + h > last or i > last - exclude_recent or not np.isfinite(vol30.iloc[i]):
                continue
            if all(abs(i - j) >= gap for j in pick):
                pick.append(i)
            if len(pick) >= k:
                break
        idx = np.array(pick)
        fwd = logc[idx + h] - logc[idx]
        r = fwd * sig_now / vol30.values[idx]
        draw = rng.integers(0, len(r), n)
        out.append((r[draw] + rng.normal(0, jitter * sig_now * math.sqrt(h), n)).astype(np.float32))
        info.append({"h": h, "k": len(idx), "match_min": int(match.loc[close.index[idx[-1]]]) if len(idx) else None,
                     "match_max": int(match.loc[close.index[idx[0]]]) if len(idx) else None, "of": len(use),
                     "p_up_raw": float((fwd > 0).mean()) if len(fwd) else None, "dates": [str(close.index[i].date()) for i in idx[:8]]})
    return np.stack(out, axis=1), info
