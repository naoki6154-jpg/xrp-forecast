"""予想エンジンの入力：リポジトリの data/raw と state/latest.json を読むだけ（書き込まない）。
全ての値に「値・時点・取得元・一次データか代わりか」を付けて返す。取れない値は None。
"""
import csv, json, math, os
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd

JST = timezone(timedelta(hours=9))


def _p(root, *a):
    return os.path.join(root, *a)


def _read(root, rel):
    path = _p(root, rel)
    if not os.path.exists(path):
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None


def _ts(s):
    if s is None or (isinstance(s, float) and math.isnan(s)):
        return None
    s = str(s).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _age_min(ts, now):
    return None if ts is None else round((now - ts).total_seconds() / 60)


def val(value, at, source, primary=True, unit=None, note=None):
    return {"value": value, "at": at, "source": source, "primary": primary, "unit": unit, "note": note}


def load_daily_usd(root):
    """XRP/USDT 日足（Binance公式一括データ）。終値の系列を返す。"""
    df = _read(root, "data/raw/prices/binance_xrp_usdt_1d.csv")
    df = df.drop_duplicates("date", keep="last").sort_values("date")
    s = pd.Series(df.close.astype(float).values, index=pd.to_datetime(df.date))
    return s


def load_daily_jpy(root):
    df = _read(root, "data/raw/prices/bitbank_xrp_jpy_1d.csv")
    df = df.drop_duplicates("date", keep="last").sort_values("date")
    return df


def current_prices(root, now, cfg):
    """現在値。ドル：Binance現物（東京経由・毎時）→ Bybit → Kraken の順。円：bitbank（1日2回）。
    bitbank が古いときは ドル価格 × ドル円（bitbankとBinanceの比から推定）で代わりに出す。"""
    max_age = cfg["freshness"]["price_max_age_min"]
    usd = None
    cands = []
    b = _read(root, "data/raw/jp_relay/binance_spot_24h_xrp.csv")
    if b is not None and len(b):
        r = b.iloc[-1]
        cands.append(("Binance 現物 XRP/USDT（東京のCloud Run経由）", float(r.lastPrice), _ts(r.time), float(r.priceChangePercent) / 100))
    y = _read(root, "data/raw/jp_relay/bybit_ticker_xrp.csv")
    if y is not None and len(y):
        r = y.iloc[-1]
        cands.append(("Bybit 先物 XRPUSDT（東京のCloud Run経由）", float(r.lastPrice), _ts(r.time), float(r.price24hPcnt)))
    k = _read(root, "data/raw/prices/kraken_xrp_usd_1d.csv")
    if k is not None and len(k):
        r = k.iloc[-1]
        prev = float(k.iloc[-2].close) if len(k) > 1 else None
        cands.append(("Kraken XRP/USD 日足（当日の途中値）", float(r.close), _ts(r.fetched_at), (float(r.close) / prev - 1) if prev else None))
    fallbacks = []
    for i, (src, px, ts, chg) in enumerate(cands):
        if ts is not None and _age_min(ts, now) <= max_age:
            usd = {"price": px, "at": ts, "source": src, "primary": i == 0, "chg24h": chg}
            break
    if usd is None and cands:  # 全部古い：一番新しいものを使い、古いと明記
        src, px, ts, chg = max(cands, key=lambda c: c[2] or datetime.min.replace(tzinfo=timezone.utc))
        usd = {"price": px, "at": ts, "source": src + "（古い値）", "primary": False, "chg24h": chg}
    if usd and not usd["primary"]:
        fallbacks.append({"item": "XRPドル価格", "used": usd["source"], "reason": "Binance現物の値が%d分より古い／取れない" % max_age})

    j = load_daily_jpy(root)
    last = j.iloc[-1]
    jts = _ts(last.fetched_at)
    prev_close = float(j.iloc[-2].close)
    # ドル円の推定（bitbank 円価格 ÷ 同時刻に近いドル価格）
    fx_implied = None
    if usd and jts and usd["at"] and abs((jts - usd["at"]).total_seconds()) < 3 * 3600:
        fx_implied = float(last.close) / usd["price"]
    fred = _read(root, "data/raw/fred/DEXJPUS.csv")
    fred_fx = None
    if fred is not None:
        f = fred.dropna(subset=["value"])
        if len(f):
            fred_fx = (float(f.iloc[-1].value), str(f.iloc[-1].date))
    if fx_implied is None and fred_fx:
        fx_implied = fred_fx[0]
    if jts and _age_min(jts, now) <= max_age:
        jpy = {"price": float(last.close), "at": jts, "source": "bitbank XRP/円（日足の当日途中値）", "primary": True}
    else:
        jpy = {"price": usd["price"] * fx_implied, "at": usd["at"], "source": "ドル価格 × ドル円（推定 %.2f）" % fx_implied, "primary": False}
        fallbacks.append({"item": "XRP円価格", "used": jpy["source"], "reason": "bitbankの値が%d分より古い（bitbankの取得は1日2回）" % max_age})
    jpy["chg_prev_close"] = jpy["price"] / prev_close - 1
    jpy["prev_close"] = prev_close
    jpy["prev_close_date"] = str(j.iloc[-2].date)
    fx = {"rate": fx_implied, "source": "bitbank円価格÷Binanceドル価格（同時刻）" if fx_implied and (not fred_fx or fx_implied != fred_fx[0]) else "FRED DEXJPUS",
          "fred": fred_fx}
    return usd, jpy, fx, fallbacks


def realized_vol(series_close, cfg):
    r = np.diff(np.log(series_close.values))
    lam = cfg["vol"]["ewma_lambda"]
    w = cfg["vol"]["window_days"]
    v = r[: 30].var()
    for x in r:
        v = lam * v + (1 - lam) * x * x
    ewma = math.sqrt(v)
    win = float(np.std(r[-w:], ddof=1))
    b = cfg["vol"]["blend_ewma"]
    daily = b * ewma + (1 - b) * win
    return {"daily": daily, "annual": daily * math.sqrt(365), "ewma_daily": ewma, "window_daily": win,
            "window_days": w, "last_date": str(series_close.index[-1].date())}


def fx_vol(root, cfg):
    f = _read(root, "data/raw/fred/DEXJPUS.csv")
    try:
        x = f.dropna(subset=["value"]).value.astype(float).values[-cfg["fx"]["window_days"]:]
        v = float(np.std(np.diff(np.log(x)), ddof=1))
        return v if v > 0 else cfg["fx"]["fallback_daily_vol"]
    except Exception:
        return cfg["fx"]["fallback_daily_vol"]


def regime(series_close, spot, cfg):
    c = cfg["regime"]
    s = series_close.copy()
    ma = s.rolling(c["ma_days"]).mean()
    ma_now = float(ma.iloc[-1]); ma_prev = float(ma.iloc[-1 - c["slope_lookback"]])
    slope = ma_now / ma_prev - 1
    r = np.log(s).diff()
    vol30 = r.rolling(c["vol_days"]).std()
    last_year = vol30.iloc[-365:]
    pct = float((last_year <= vol30.iloc[-1]).mean())
    dev = spot / ma_now - 1
    if pct >= c["crisis_pct"]:
        state = "荒れ"
    elif dev > c["range_band"] and slope > 0:
        state = "上昇"
    elif dev < -c["range_band"] and slope < 0:
        state = "下落"
    elif abs(dev) <= c["range_band"]:
        state = "横ばい"
    else:
        state = "下落" if dev < 0 else "上昇"
    return {"state": state, "ma200_usd": ma_now, "dev_from_ma200": dev, "ma200_slope_20d": slope,
            "vol30_daily": float(vol30.iloc[-1]), "vol30_pct_1y": pct,
            "rule": "200日線との位置（±5%）と200日線の20日の傾き。30日変動率が過去1年の上位5%なら「荒れ」"}


def fed_market(root):
    """Polymarket の「2026年末の政策金利の上限」から、12月FOMCの利上げ確率を計算する。"""
    tar = _read(root, "data/raw/fred/DFEDTARU.csv")
    pm = _read(root, "data/raw/prediction_markets/polymarket_daily.csv")
    if tar is None or pm is None:
        return None
    t = tar.dropna(subset=["value"])
    cur = float(t.iloc[-1].value); cur_date = str(t.iloc[-1].date)
    d = pm[pm.date == pm.date.max()]
    rows = d[d.question.str.contains("upper bound of the target federal funds rate", na=False) & d.question.str.contains("end of 2026", na=False)]
    probs = {}
    import re
    for _, r in rows.iterrows():
        try:
            p = float(json.loads(r.outcome_prices)[0])
        except Exception:
            continue
        q = r.question
        m = re.search(r"(≥|≤)?\s*(\d+(?:\.\d+)?)%", q)
        if not m:
            continue
        lvl = float(m.group(2)); sign = m.group(1)
        probs[(sign or "") + str(lvl)] = (lvl, sign, p, int(r.market_id), float(r.volume or 0))
    if not probs:
        return None
    tot = sum(v[2] for v in probs.values())
    up = sum(v[2] for v in probs.values() if v[0] > cur + 1e-9 or (v[1] == "≥" and v[0] > cur))
    down = sum(v[2] for v in probs.values() if v[0] < cur - 1e-9 and v[1] != "≥")
    same = tot - up - down
    return {"current_upper": cur, "current_date": cur_date, "p_hike": up / tot, "p_hold": same / tot, "p_cut": down / tot,
            "raw_sum": tot, "n_markets": len(probs), "date": str(d.date.max()),
            "source": "Polymarket gamma-api（年末の政策金利の上限、%d市場）＋FRED DFEDTARU" % len(probs)}


def derivatives(root):
    out = {"funding": [], "oi": [], "lsr": []}
    def last(rel):
        d = _read(root, rel)
        return None if d is None or not len(d) else d.iloc[-1]
    r = last("data/raw/jp_relay/binance_funding_xrp.csv")
    if r is not None: out["funding"].append({"ex": "Binance", "value": float(r.fundingRate), "at": r.time, "source": "Binance fapi fundingRate（東京経由）"})
    r = last("data/raw/jp_relay/bybit_funding_xrp.csv")
    if r is not None: out["funding"].append({"ex": "Bybit", "value": float(r.fundingRate), "at": r.time, "source": "Bybit funding/history（東京経由）"})
    r = last("data/raw/derivatives/okx_funding_xrp.csv")
    if r is not None: out["funding"].append({"ex": "OKX", "value": float(r.funding_rate), "at": r.time, "source": "OKX funding-rate-history"})
    spot = last("data/raw/jp_relay/binance_spot_24h_xrp.csv")
    r = last("data/raw/jp_relay/binance_oi_now_xrp.csv")
    if r is not None and spot is not None:
        out["oi"].append({"ex": "Binance", "value": float(r.openInterest) * float(spot.lastPrice), "xrp": float(r.openInterest), "at": r.time, "source": "Binance fapi openInterest × 現在値（東京経由）"})
    r = last("data/raw/jp_relay/bybit_ticker_xrp.csv")
    if r is not None: out["oi"].append({"ex": "Bybit", "value": float(r.openInterestValue), "xrp": float(r.openInterest), "at": r.time, "source": "Bybit tickers openInterestValue（東京経由）"})
    r = last("data/raw/derivatives/okx_oi_xrp.csv")
    if r is not None: out["oi"].append({"ex": "OKX", "value": float(r.open_interest_usd), "at": r.date, "source": "OKX rubik open-interest 日次"})
    r = last("data/raw/jp_relay/binance_lsr_global_1h_xrp.csv")
    if r is not None: out["lsr"].append({"ex": "Binance", "value": float(r.longShortRatio), "long": float(r.longAccount), "at": r.time, "source": "Binance globalLongShortAccountRatio 1h（口座数）"})
    r = last("data/raw/jp_relay/bybit_lsr_1h_xrp.csv")
    if r is not None: out["lsr"].append({"ex": "Bybit", "value": float(r.buyRatio) / float(r.sellRatio), "long": float(r.buyRatio), "at": r.time, "source": "Bybit account-ratio 1h（口座数）"})
    r = last("data/raw/derivatives/okx_lsr_xrp.csv")
    if r is not None: out["lsr"].append({"ex": "OKX", "value": float(r.long_short_ratio), "at": r.date, "source": "OKX rubik long-short-account-ratio 日次"})
    return out


def macro(root, now):
    out = {}
    fg = _read(root, "data/raw/sentiment/fear_greed.csv")
    if fg is not None and len(fg):
        r = fg.iloc[-1]
        out["fear_greed"] = {"value": int(r.value), "label": r.classification, "at": r.date, "source": "alternative.me"}
        out["fear_greed_hist"] = [[str(a), int(b)] for a, b in zip(fg.date.iloc[-30:], fg.value.iloc[-30:])]
    cal = []
    today = now.astimezone(JST).date().isoformat()
    for fid, name in (("10", "米CPI"), ("50", "米雇用統計"), ("54", "米PCE")):
        d = _read(root, "data/raw/fred/release_dates_%s.csv" % fid)
        if d is None:
            continue
        fut = d[d.release_date >= today].sort_values("release_date")
        if len(fut):
            cal.append({"name": name, "date": str(fut.iloc[0].release_date), "consensus": None, "source": "FRED release/dates（時刻なし）"})
    out["calendar"] = sorted(cal, key=lambda x: x["date"])
    return out


def fetch_status(root, now):
    st = json.load(open(_p(root, "state/latest.json")))
    src = st.get("sources", {})
    log = _read(root, "data/raw/_fetch_log.csv")
    gha = None
    if log is not None and len(log):
        last_run = log.run_at.max()
        lr = log[log.run_at == last_run]
        gha = {"last_at": last_run, "ok": bool(lr.ok.astype(int).all()), "n": int(len(lr)),
               "failed": [s for s, o in zip(lr.source, lr.ok) if int(o) != 1]}
    any_fail = [k for k, v in src.items() if not v.get("ok")]
    newest = max((v.get("fetched_at") for v in src.values() if v.get("fetched_at")), default=None)
    gh = {"name": "GitHub Actions", "last_at": newest, "ok": not any_fail, "failed": any_fail,
          "note": "毎時5分（東京経由の分）と 07:15・19:15（全取得先）"}
    jp = src.get("jp_relay", {})
    jpc = {"name": "日本のクラウド（Cloud Run 東京）", "last_at": jp.get("fetched_at"), "ok": jp.get("ok"), "failed": [] if jp.get("ok") else ["jp_relay"],
           "note": jp.get("note")}
    mac = {"name": "Mac（補助）", "last_at": None, "ok": None, "failed": [], "note": "準備中（未設定。設計書3-4の方法B）"}
    fresh = []
    for k, v in src.items():
        ts = _ts(v.get("fetched_at"))
        fresh.append({"source": k, "fetched_at": v.get("fetched_at"), "age_min": _age_min(ts, now), "ok": v.get("ok"),
                      "last_data": v.get("last"), "primary": True, "note": v.get("note") or v.get("error")})
    return {"fetchers": [gh, jpc, mac], "freshness": fresh, "state_updated_at": st.get("updated_at"), "stage_status": st.get("status")}
