"""指標パネル（設計書 第11章 区画5）：7分野の主な指標を「追い風・中立・逆風」に分ける。
分け方は下の RULES の決まった規則だけで行う（AIは判定しない）。しきい値は判断で置いたもので、検証（段階3）で見直す。
予想の計算には使わない（表示用）。値・時点・取得元を必ず付け、取れない値は None。
"""
import os
import numpy as np
import pandas as pd

RULES_BASIS = "判断（しきい値は段階3の検証で見直す）"


def _csv(root, rel):
    p = os.path.join(root, rel)
    if not os.path.exists(p):
        return None
    try:
        return pd.read_csv(p)
    except Exception:
        return None


def _spark(series, n=30):
    s = series.dropna().iloc[-n:]
    return [[str(i)[:10], round(float(v), 6)] for i, v in s.items()]


def _wind(v, pos, neg, higher_is_good=True):
    if v is None or not np.isfinite(v):
        return None
    if higher_is_good:
        return "追い風" if v >= pos else "逆風" if v <= neg else "中立"
    return "逆風" if v >= pos else "追い風" if v <= neg else "中立"


def build(root, close_usd, fed):
    out = []

    def add(domain, name, value, unit, wind, rule, at, source, spark=None, note=None):
        out.append({"domain": domain, "name": name, "value": None if value is None or not np.isfinite(value) else float(value), "unit": unit,
                    "wind": wind, "rule": rule, "at": at, "source": source, "spark": spark, "note": note})

    s = close_usd.astype(float)
    dev = np.log(s / s.rolling(200).mean())
    add("トレンド", "200日線からの乖離", dev.iloc[-1], "比", _wind(dev.iloc[-1], 0.05, -0.05), "+5%以上で追い風、−5%以下で逆風",
        str(s.index[-1].date()), "Binance XRP/USDT 日足", _spark(dev))
    r30 = np.log(s / s.shift(30))
    add("勢い", "30日の騰落率", r30.iloc[-1], "比", _wind(r30.iloc[-1], 0.10, -0.10), "+10%以上で追い風、−10%以下で逆風",
        str(s.index[-1].date()), "Binance XRP/USDT 日足", _spark(r30))

    fr = []
    for rel, col, ex in (("data/raw/jp_relay/binance_funding_xrp.csv", "fundingRate", "Binance"), ("data/raw/jp_relay/bybit_funding_xrp.csv", "fundingRate", "Bybit"),
                         ("data/raw/derivatives/okx_funding_xrp.csv", "funding_rate", "OKX")):
        d = _csv(root, rel)
        if d is not None and len(d):
            fr.append((ex, float(d.iloc[-1][col]), str(d.iloc[-1]["time"])))
    if fr:
        v = float(np.mean([x[1] for x in fr]))
        add("ポジション", "資金調達率（平均・1回分）", v, "比", _wind(v, 0.0003, -0.0001, higher_is_good=False),
            "+0.03%以上はロング過熱で逆風、−0.01%以下はショート過多（踏み上げの燃料）で追い風", fr[0][2], "、".join(x[0] for x in fr))
    lsr = _csv(root, "data/raw/jp_relay/binance_lsr_global_1h_xrp.csv")
    if lsr is not None and len(lsr):
        sp = pd.Series(lsr.longShortRatio.astype(float).values, index=pd.to_datetime(lsr.time))
        v = float(sp.iloc[-1])
        add("ポジション", "ロング・ショート比率（Binance 口座数）", v, "倍", _wind(v, 3.0, 1.0, higher_is_good=False),
            "3倍以上はロング偏りで逆風、1倍以下はショート偏りで追い風", str(lsr.iloc[-1]["time"]), "Binance globalLongShortAccountRatio", _spark(sp, 48))

    st = _csv(root, "data/raw/flows/stablecoins_all.csv")
    if st is not None and len(st) > 31:
        ss = pd.Series(st.circulating_usd.astype(float).values, index=pd.to_datetime(st.date))
        ch = ss / ss.shift(30) - 1
        add("資金の流れ", "ステーブルコイン供給量の30日の変化", ch.iloc[-1], "比", _wind(ch.iloc[-1], 0.01, -0.01), "+1%以上で追い風、−1%以下で逆風",
            str(ss.index[-1].date()), "DefiLlama", _spark(ch))
    etf = _csv(root, "data/raw/flows/etf_flows_btc.csv")
    if etf is not None and len(etf) and "total_usd_m" in etf:
        e = pd.Series(pd.to_numeric(etf.total_usd_m, errors="coerce").values, index=pd.to_datetime(etf.date)).dropna()
        s5 = e.rolling(5).sum()
        if len(s5.dropna()):
            add("資金の流れ", "BTC現物ETFの5日の資金の流れ", s5.iloc[-1], "百万ドル", _wind(s5.iloc[-1], 0, 0) if s5.iloc[-1] != 0 else "中立",
                "流入で追い風、流出で逆風（XRPのETFは未取得のためBTCで代用）", str(e.index[-1].date()), "Farside Investors", _spark(s5))

    if fed and fed.get("p_hike") is not None:
        add("マクロ", "12月FOMCの利上げ確率", fed["p_hike"], "確率", _wind(fed["p_hike"], 0.6, 0.3, higher_is_good=False), "60%以上で逆風、30%以下で追い風",
            fed.get("date"), "Polymarket＋FRED")
    for sid, name, rule, pos, neg, good in (("DTWEXBGS", "ドル指数の20日の変化", "+1%以上はドル高で逆風、−1%以下で追い風", 0.01, -0.01, False),
                                           ("NASDAQCOM", "ナスダックの20日の変化", "+3%以上で追い風、−3%以下で逆風", 0.03, -0.03, True)):
        d = _csv(root, "data/raw/fred/%s.csv" % sid)
        if d is not None:
            x = pd.Series(pd.to_numeric(d.value, errors="coerce").values, index=pd.to_datetime(d.date)).dropna()
            if len(x) > 21:
                ch = x / x.shift(20) - 1
                add("マクロ", name, ch.iloc[-1], "比", _wind(ch.iloc[-1], pos, neg, good), rule, str(x.index[-1].date()), "FRED " + sid, _spark(ch))

    fg = _csv(root, "data/raw/sentiment/fear_greed.csv")
    if fg is not None and len(fg):
        f = pd.Series(fg.value.astype(float).values, index=pd.to_datetime(fg.date))
        v = float(f.iloc[-1])
        add("心理", "恐怖強欲指数", v, "点", "中立" if 25 < v < 75 else ("追い風" if v <= 25 else "逆風"),
            "25以下（極端な恐怖）は逆張りで追い風、75以上（極端な強欲）は逆風", str(f.index[-1].date()), "alternative.me", _spark(f))

    sb = _csv(root, "data/raw/onchain/stolen_xrp_balances.csv")
    if sb is not None and len(sb) and "balance_xrp" in sb:
        snap = sb.groupby("time").balance_xrp.sum()          # 取得の回ごとの合計
        snap.index = pd.to_datetime(snap.index)
        tot = snap.groupby(snap.index.date).last()             # 1日の最後の回
        tot.index = pd.to_datetime(tot.index)
        ch = tot.diff()
        v = float(tot.iloc[-1])
        add("オンチェーン", "追跡中の盗難XRPの残高", v / 1e6, "百万XRP", "逆風" if len(ch.dropna()) and ch.iloc[-1] < 0 else "中立",
            "前回より減った（売りに回った可能性）なら逆風、それ以外は中立", str(tot.index[-1].date()), "XRPレジャー公開ノード", _spark(tot / 1e6))
    else:
        add("オンチェーン", "追跡中の盗難XRPの残高", None, "百万XRP", None, "—", None, "XRPレジャー公開ノード", note="列の形を確認中")

    winds = [x["wind"] for x in out if x["wind"]]
    score = sum(1 if w == "追い風" else -1 if w == "逆風" else 0 for w in winds)
    return {"items": out, "count": {"追い風": winds.count("追い風"), "中立": winds.count("中立"), "逆風": winds.count("逆風")}, "score": score,
            "basis": RULES_BASIS, "note": "表示用。予想の計算には使っていない"}
