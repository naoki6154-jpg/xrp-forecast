"""取得先ごとの取得プログラム。

各関数は {"rows": 全体の行数, "added": 増えた行数, "last": 最新の日付, "note": メモ} を返す。
失敗したら例外を投げ、run.py が記録して次の取得先に進む（全体は止めない）。
"""
import csv
import io
import os
from datetime import date, datetime, timezone, timedelta

from common import (RAW, http_get, merge_csv, merge_csv_dynamic, ms_to_date, ms_to_iso, now_utc_iso,
                    read_csv_keys, last_value, unzip_single_csv)

OHLC_FIELDS = ["date", "open", "high", "low", "close", "volume", "partial", "source", "fetched_at"]
SYMBOLS = ["XRP", "BTC", "ETH", "XLM"]


def _partial(day_start_ms, fetched):
    """日足がまだ確定していない（その日が終わっていない）なら 1"""
    end = datetime.fromtimestamp(int(day_start_ms) / 1000, tz=timezone.utc) + timedelta(days=1)
    return "1" if end > fetched else "0"


# ---------- 価格：bitbank（円建て、XRP/円の一次データ） ----------
def bitbank_daily():
    fetched_at = now_utc_iso()
    fetched = datetime.now(timezone.utc)
    path = os.path.join(RAW, "prices", "bitbank_xrp_jpy_1d.csv")
    have = read_csv_keys(path, "date")
    this_year = fetched.year
    years = range(2018, this_year + 1) if not have else [this_year - 1, this_year] if fetched.month == 1 else [this_year]
    rows = []
    for y in years:
        d = http_get(f"https://public.bitbank.cc/xrp_jpy/candlestick/1day/{y}")
        if d.get("success") != 1:
            raise RuntimeError(f"bitbank error {d}")
        for o, h, l, c, v, ts in d["data"]["candlestick"][0]["ohlcv"]:
            rows.append(dict(date=ms_to_date(ts), open=o, high=h, low=l, close=c, volume=v,
                             partial=_partial(ts, fetched), source="bitbank public API candlestick/1day",
                             fetched_at=fetched_at))
    total, added = merge_csv(path, rows, ["date"], OHLC_FIELDS)
    return dict(rows=total, added=added, last=last_value(path, "date"),
                note="日足の区切りは bitbank の仕様に従う（タイムスタンプをUTC日付で記録）")


# ---------- 価格：Kraken（ドル建て、直近約720日） ----------
KRAKEN_PAIRS = {"XRP": "XRPUSD", "BTC": "XBTUSD", "ETH": "ETHUSD", "XLM": "XLMUSD"}


def kraken_daily():
    fetched_at = now_utc_iso()
    fetched = datetime.now(timezone.utc)
    out = {}
    for sym, pair in KRAKEN_PAIRS.items():
        d = http_get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440")
        if d.get("error"):
            raise RuntimeError(f"kraken {pair}: {d['error']}")
        key = [k for k in d["result"] if k != "last"][0]
        rows = []
        for t, o, h, l, c, vwap, v, n in d["result"][key]:
            rows.append(dict(date=ms_to_date(int(t) * 1000), open=o, high=h, low=l, close=c, volume=v,
                             partial=_partial(int(t) * 1000, fetched),
                             source=f"Kraken public API OHLC {pair} 1440", fetched_at=fetched_at))
        path = os.path.join(RAW, "prices", f"kraken_{sym.lower()}_usd_1d.csv")
        total, added = merge_csv(path, rows, ["date"], OHLC_FIELDS)
        out[sym] = (total, added, last_value(path, "date"))
    return dict(rows=sum(v[0] for v in out.values()), added=sum(v[1] for v in out.values()),
                last=min(v[2] for v in out.values()), note=", ".join(f"{k}:{v[0]}行" for k, v in out.items()))


# ---------- 価格：Binance 公式一括データ（2018年以降の過去分） ----------
def binance_vision_daily():
    """data.binance.vision の月次・日次ファイル。米国のサーバーからも取れる静的ファイル。"""
    fetched_at = now_utc_iso()
    today = datetime.now(timezone.utc).date()
    base = "https://data.binance.vision/data/spot"
    summary = {}
    for sym in SYMBOLS:
        pair = f"{sym}USDT"
        path = os.path.join(RAW, "prices", f"binance_{sym.lower()}_usdt_1d.csv")
        have = read_csv_keys(path, "date")
        have_months = {d[:7] for d in have}
        rows = []
        # 月次ファイル：2018-01 〜 先月。すでに持っている月は取らない
        y, m = 2018, 1
        while (y, m) < (today.year, today.month):
            ym = f"{y:04d}-{m:02d}"
            if ym not in have_months or (y, m) == ((today.replace(day=1) - timedelta(days=1)).year,
                                               (today.replace(day=1) - timedelta(days=1)).month):
                try:
                    blob = http_get(f"{base}/monthly/klines/{pair}/1d/{pair}-1d-{ym}.zip", as_json=False)
                    rows += _binance_rows(unzip_single_csv(blob), f"data.binance.vision monthly {pair} 1d", fetched_at)
                except Exception:  # noqa: BLE001  まだ公開されていない月・上場前の月
                    pass
            m += 1
            if m == 13:
                y, m = y + 1, 1
        # 今月分は日次ファイル（前日まで）
        d = today.replace(day=1)
        while d < today:
            ds = d.isoformat()
            if ds not in have:
                try:
                    blob = http_get(f"{base}/daily/klines/{pair}/1d/{pair}-1d-{ds}.zip", as_json=False)
                    rows += _binance_rows(unzip_single_csv(blob), f"data.binance.vision daily {pair} 1d", fetched_at)
                except Exception:  # noqa: BLE001
                    pass
            d += timedelta(days=1)
        total, added = merge_csv(path, rows, ["date"], OHLC_FIELDS)
        summary[sym] = (total, added, last_value(path, "date"))
    return dict(rows=sum(v[0] for v in summary.values()), added=sum(v[1] for v in summary.values()),
                last=min(v[2] for v in summary.values() if v[2]) if any(v[2] for v in summary.values()) else "",
                note=", ".join(f"{k}:{v[0]}行" for k, v in summary.items()))


def _binance_rows(text, source, fetched_at):
    rows = []
    for r in csv.reader(io.StringIO(text)):
        if not r or not r[0].isdigit():
            continue  # 見出し行
        rows.append(dict(date=ms_to_date(r[0]), open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5],
                         partial="0", source=source, fetched_at=fetched_at))
    return rows


# ---------- 心理：恐怖強欲指数（alternative.me、2018年2月〜） ----------
def fear_greed():
    fetched_at = now_utc_iso()
    d = http_get("https://api.alternative.me/fng/?limit=0&format=json")
    rows = [dict(date=ms_to_date(int(x["timestamp"]) * 1000), value=x["value"],
                 classification=x["value_classification"], source="alternative.me fng API",
                 fetched_at=fetched_at) for x in d["data"]]
    path = os.path.join(RAW, "sentiment", "fear_greed.csv")
    total, added = merge_csv(path, rows, ["date"], ["date", "value", "classification", "source", "fetched_at"])
    return dict(rows=total, added=added, last=last_value(path, "date"), note="")


# ---------- デリバティブ：OKX（資金調達率・建玉・ロングショート比） ----------
def okx_derivatives():
    fetched_at = now_utc_iso()
    notes = []
    total_rows = added_rows = 0
    lasts = []
    for sym in ["XRP", "BTC"]:
        # 資金調達率の履歴（OKXは直近約3ヶ月分。毎日取って積み上げる）
        inst = f"{sym}-USDT-SWAP"
        rows, after = [], ""
        for _ in range(10):
            url = f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst}&limit=100"
            if after:
                url += f"&after={after}"
            d = http_get(url)
            if d.get("code") != "0":
                raise RuntimeError(f"okx funding {inst}: {d.get('msg')}")
            if not d["data"]:
                break
            for x in d["data"]:
                rows.append(dict(time=ms_to_iso(x["fundingTime"]), inst=inst, funding_rate=x["fundingRate"],
                                 realized_rate=x.get("realizedRate", ""),
                                 source="OKX public API funding-rate-history", fetched_at=fetched_at))
            after = d["data"][-1]["fundingTime"]
        path = os.path.join(RAW, "derivatives", f"okx_funding_{sym.lower()}.csv")
        t, a = merge_csv(path, rows, ["time"], ["time", "inst", "funding_rate", "realized_rate", "source", "fetched_at"])
        total_rows += t; added_rows += a; lasts.append(last_value(path, "time")[:10])

        # 建玉と出来高（日次）
        d = http_get(f"https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?ccy={sym}&period=1D")
        if d.get("code") == "0":
            rows = [dict(date=ms_to_date(x[0]), ccy=sym, open_interest_usd=x[1], volume_usd=x[2],
                         source="OKX rubik open-interest-volume 1D", fetched_at=fetched_at) for x in d["data"]]
            path = os.path.join(RAW, "derivatives", f"okx_oi_{sym.lower()}.csv")
            t, a = merge_csv(path, rows, ["date"], ["date", "ccy", "open_interest_usd", "volume_usd", "source", "fetched_at"])
            total_rows += t; added_rows += a
        else:
            notes.append(f"OI {sym}: {d.get('msg')}")

        # ロング・ショートの口座数の比率（日次）
        d = http_get(f"https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={sym}&period=1D")
        if d.get("code") == "0":
            rows = [dict(date=ms_to_date(x[0]), ccy=sym, long_short_ratio=x[1],
                         source="OKX rubik long-short-account-ratio 1D", fetched_at=fetched_at) for x in d["data"]]
            path = os.path.join(RAW, "derivatives", f"okx_lsr_{sym.lower()}.csv")
            t, a = merge_csv(path, rows, ["date"], ["date", "ccy", "long_short_ratio", "source", "fetched_at"])
            total_rows += t; added_rows += a
        else:
            notes.append(f"LSR {sym}: {d.get('msg')}")
    return dict(rows=total_rows, added=added_rows, last=min(lasts), note="; ".join(notes))


# ---------- マクロ：FRED（米国の経済統計） ----------
# 系列ID: 説明
FRED_SERIES = {
    "CPIAUCSL": "CPI（総合、季節調整済み）",
    "CPILFESL": "コアCPI（食品・エネルギー除く）",
    "PCEPI": "PCE物価指数",
    "PCEPILFE": "コアPCE物価指数",
    "PAYEMS": "非農業部門雇用者数",
    "UNRATE": "失業率",
    "DFF": "FF金利（実効、日次）",
    "DFEDTARU": "FF金利の誘導目標（上限）",
    "DGS2": "米国債2年利回り",
    "DGS10": "米国債10年利回り",
    "T10YIE": "10年期待インフレ率",
    "DTWEXBGS": "ドル指数（広義、FRB）",
    "DEXJPUS": "ドル円（FRB、NY正午）",
    "DCOILWTICO": "原油（WTI）",
    "NASDAQCOM": "ナスダック総合指数",
}
# 「発表当日の値（初回発表値）」も取る系列（後の修正値を使わないため）
FRED_FIRST_RELEASE = ["CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE", "PAYEMS", "UNRATE"]
# 発表日の一覧を取る統計（FREDのrelease_id）
FRED_RELEASES = {10: "CPI", 50: "雇用統計", 54: "個人所得・支出（PCE）"}


def fred():
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY が未設定（GitHubのSecretsを確認）")
    fetched_at = now_utc_iso()
    api = "https://api.stlouisfed.org/fred"
    total = added = 0
    lasts = []
    fields = ["date", "value", "series_id", "realtime_start", "source", "fetched_at"]
    for sid in FRED_SERIES:
        d = http_get(f"{api}/series/observations?series_id={sid}&api_key={key}&file_type=json"
                     f"&observation_start=2014-01-01")
        rows = [dict(date=x["date"], value=("" if x["value"] == "." else x["value"]), series_id=sid,
                     realtime_start=x["realtime_start"], source="FRED API series/observations（最新の改定値）",
                     fetched_at=fetched_at) for x in d["observations"]]
        path = os.path.join(RAW, "fred", f"{sid}.csv")
        t, a = merge_csv(path, rows, ["date"], fields)
        total += t; added += a; lasts.append(last_value(path, "date"))
    for sid in FRED_FIRST_RELEASE:
        d = http_get(f"{api}/series/observations?series_id={sid}&api_key={key}&file_type=json"
                     f"&observation_start=2014-01-01&output_type=4"
                     f"&realtime_start=1776-07-04&realtime_end=9999-12-31")
        rows = [dict(date=x["date"], value=("" if x["value"] == "." else x["value"]), series_id=sid,
                     realtime_start=x["realtime_start"],
                     source="FRED(ALFRED) API output_type=4（初回発表値。realtime_start=発表日）",
                     fetched_at=fetched_at) for x in d["observations"]]
        path = os.path.join(RAW, "fred", f"{sid}_first_release.csv")
        t, a = merge_csv(path, rows, ["date"], fields)
        total += t; added += a
    for rid, name in FRED_RELEASES.items():
        d = http_get(f"{api}/release/dates?release_id={rid}&api_key={key}&file_type=json"
                     f"&realtime_start=2014-01-01&realtime_end=9999-12-31"
                     f"&include_release_dates_with_no_data=true&sort_order=asc&limit=10000")
        rows = [dict(release_date=x["date"], release_id=rid, release_name=name,
                     source="FRED API release/dates（時刻は含まない）", fetched_at=fetched_at)
                for x in d["release_dates"]]
        path = os.path.join(RAW, "fred", f"release_dates_{rid}.csv")
        t, a = merge_csv(path, rows, ["release_date"], ["release_date", "release_id", "release_name", "source", "fetched_at"])
        total += t; added += a
    return dict(rows=total, added=added, last=max(lasts), note=f"{len(FRED_SERIES)}系列＋初回発表値{len(FRED_FIRST_RELEASE)}系列＋発表日{len(FRED_RELEASES)}種")


# ---------- 予測市場：Polymarket（暗号資産・FRB関連の現在の確率、毎日の記録） ----------
def polymarket():
    fetched_at = now_utc_iso()
    rows = []
    for tag in ["xrp", "fed", "inflation"]:
        d = http_get(f"https://gamma-api.polymarket.com/events?closed=false&limit=50&tag_slug={tag}")
        for ev in d:
            for mk in ev.get("markets", []):
                rows.append(dict(date=fetched_at[:10], market_id=mk.get("id", ""), tag=tag,
                                 question=mk.get("question", ""), outcomes=mk.get("outcomes", ""),
                                 outcome_prices=mk.get("outcomePrices", ""), end_date=mk.get("endDate", ""),
                                 volume=mk.get("volume", ""), source="Polymarket gamma-api events",
                                 fetched_at=fetched_at))
    path = os.path.join(RAW, "prediction_markets", "polymarket_daily.csv")
    total, added = merge_csv(path, rows, ["date", "market_id"],
                             ["date", "market_id", "tag", "question", "outcomes", "outcome_prices",
                              "end_date", "volume", "source", "fetched_at"])
    return dict(rows=total, added=added, last=fetched_at[:10], note=f"本日 {len(rows)}市場")


# ---------- 日本の中継係経由：Binance・Bybit（設計書 3-4 方法C） ----------
# 名前: (返ってきたJSONの中で記録の一覧がある場所, 時刻の項目名)。None は「現在値」1件。
JP_LISTS = {
    "binance_oi_hist_1h": ([], "timestamp"),
    "binance_lsr_global_1h": ([], "timestamp"),
    "binance_lsr_top_pos_1h": ([], "timestamp"),
    "binance_taker_ratio_1h": ([], "timestamp"),
    "binance_funding": ([], "fundingTime"),
    "bybit_oi_1h": (["result", "list"], "timestamp"),
    "bybit_lsr_1h": (["result", "list"], "timestamp"),
    "bybit_funding": (["result", "list"], "fundingRateTimestamp"),
}
JP_SNAPSHOTS = {
    "binance_premium": [],
    "binance_oi_now": [],
    "binance_spot_24h": [],
    "bybit_ticker": ["result", "list", 0],
}


def _rename_time(rec):
    """取引所の項目名 time / symbol は、こちらの列と重なるので api_ を付けて残す"""
    return {("api_" + k if k in ("time", "symbol", "source", "fetched_at") else k): v for k, v in rec.items()}


def _dig(obj, path):
    for p in path:
        obj = obj[p]
    return obj


def jp_relay():
    """東京の Cloud Run（中継係）から Binance・Bybit の公開データを受け取り、そのまま保存する。"""
    url = os.environ.get("JP_RELAY_URL", "").strip().rstrip("/")
    token = os.environ.get("JP_RELAY_TOKEN", "").strip()
    if not url or not token:
        raise RuntimeError("JP_RELAY_URL / JP_RELAY_TOKEN が未設定（GitHubのSecretsを確認）")
    import json
    import urllib.request
    req = urllib.request.Request(f"{url}/bundle?symbols=XRP,BTC",
                                 headers={"X-Relay-Token": token, "User-Agent": "xrp-forecast-fetcher"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read())
    total = added = 0
    ng = []
    base = os.path.join(RAW, "jp_relay")
    for it in d["items"]:
        name, sym = it["name"], it["symbol"]
        if it.get("status") != 200 or "body" not in it:
            ng.append(f"{name}/{sym}:{it.get('status')} {it.get('error', '')[:60]}")
            continue
        meta = dict(symbol=sym, source=f"{it['url']}（東京のCloud Run経由）", fetched_at=it["received_at"])
        try:
            if name in JP_LISTS:
                path_, tfield = JP_LISTS[name]
                recs = _dig(it["body"], path_)
                rows = [{"time": ms_to_iso(rec[tfield]), **_rename_time(rec), **meta} for rec in recs]
                t, a = merge_csv_dynamic(os.path.join(base, f"{name}_{sym.lower()}.csv"), rows, ["time"],
                                         first_fields=["time"])
            else:
                rec = _dig(it["body"], JP_SNAPSHOTS[name])
                rows = [{"time": it["received_at"][:19] + "Z",
                         **{k: v for k, v in _rename_time(rec).items() if not isinstance(v, (list, dict))}, **meta}]
                t, a = merge_csv_dynamic(os.path.join(base, f"{name}_{sym.lower()}.csv"), rows, ["time"],
                                         first_fields=["time"])
            total += t; added += a
        except Exception as e:  # noqa: BLE001  形が想定と違った（取引所の仕様変更など）
            ng.append(f"{name}/{sym}: 形が想定外 {type(e).__name__}")
    if ng and added == 0:
        raise RuntimeError("中継係から1件も取れず：" + "; ".join(ng)[:250])
    return dict(rows=total, added=added, last=d.get("generated_at", "")[:10],
                note=("取れなかった項目：" + "; ".join(ng)[:250]) if ng else "全項目取得")


ALL = {
    "bitbank": bitbank_daily,
    "kraken": kraken_daily,
    "binance_vision": binance_vision_daily,
    "fear_greed": fear_greed,
    "okx": okx_derivatives,
    "fred": fred,
    "polymarket": polymarket,
    "jp_relay": jp_relay,
}
