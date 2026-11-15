"""売買の偏り・季節指数・月別騰落率（参考表示）。2026-11-15 追加。予想への重みは0（確率・範囲の計算には一切入れない）。

設計書 追補 第18章。
  taker    テイカー売買比率（成行買い出来高÷出来高）。data/raw/flows/binance_taker_xrp_1d.csv が出来たら表示。無ければ「準備中」
  altseason アルトコイン季節指数。data/raw/sentiment/altseason.csv が出来たら表示。無ければ「準備中」
  monthly  月別の騰落率（XRP・BTC、Binance USDT日足の終値、月初の前日終値→月末終値）。保存済みの日足から計算
データが無い項目は state="準備中"、値は入れない（推測で埋めない）。
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"


def _close(name):
    p = RAW / "prices" / name
    if not p.exists():
        return None
    s = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date").set_index("date")["close"].astype(float)
    return s


def _monthly(s):
    if s is None or len(s) < 60:
        return None
    m = s.resample("ME").last()
    if s.index[-1] < m.index[-1]:
        m = m.iloc[:-1]  # 途中の月（まだ終わっていない月）は入れない
    r = (m / m.shift(1) - 1).dropna()
    tbl = {}
    for mon in range(1, 13):
        v = r[r.index.month == mon]
        if len(v):
            tbl[str(mon)] = {"n": int(len(v)), "mean_pct": round(float(v.mean()) * 100, 1), "median_pct": round(float(v.median()) * 100, 1),
                              "up": round(float((v > 0).mean()), 2), "last_year": int(v.index[-1].year), "last_pct": round(float(v.iloc[-1]) * 100, 1)}
    return tbl


def _file_state(path, label):
    p = RAW / path
    if not p.exists():
        return {"state": "準備中", "note": label + "のデータがまだ無い（🗄️データ基盤の取得待ち）"}
    try:
        d = pd.read_csv(p)
        last = d.iloc[-1].to_dict()
        return {"state": "表示中", "rows": int(len(d)), "last": {k: (None if pd.isna(v) else v) for k, v in last.items()}}
    except Exception as ex:
        return {"state": "エラー", "note": str(ex)}


def analyze():
    xrp = _close("binance_xrp_usdt_1d.csv")
    btc = _close("binance_btc_usdt_1d.csv")
    return {
        "weight": 0,
        "note": "参考表示（予想の確率・範囲には入らない）。採用は前進型の検証に合格後、🔬検証・改良が判断（設計書 第18章）",
        "taker": _file_state("flows/binance_taker_xrp_1d.csv", "テイカー売買比率"),
        "altseason": _file_state("sentiment/altseason.csv", "アルトコイン季節指数"),
        "monthly": {"basis": "Binance USDT日足の終値。月末終値÷前月末終値", "xrp": _monthly(xrp), "btc": _monthly(btc),
                    "caveat": "年数分（約8年）の件数しかなく、予想には使わない"},
    }
