"""参考表示（設計書 追補 第18章）用のデータの取得：テイカー売買比率（①）。

- 過去分：Binance 公式一括データ（data.binance.vision）の現物の日足（月次・日次ファイル）。
  日足ファイルの列：open_time, open, high, low, close, volume, close_time, quote_volume, trades,
  taker_buy_base_volume, taker_buy_quote_volume, ignore（Binance の klines の仕様）。
- 当日分（途中）：Binance の公開データ専用の API（data-api.binance.vision、認証不要）の /api/v3/klines。
  取れないときは当日の行を作らない（推測で埋めない）。
- 出力：data/raw/flows/binance_taker_{xrp,btc}_1d.csv
  列：date, volume, taker_buy_volume, taker_ratio, partial, source, fetched_at
  taker_ratio = taker_buy_volume ÷ volume（小数6桁。volume が0や空なら空欄）。当日の途中は partial=1。
- 状況：data/raw/flows/refflow_status.json（state/latest.json と _fetch_log.csv は書かない。毎時の取得と保存がぶつからないように）

既存の data/raw/prices/binance_*_usdt_1d.csv は変更しない。
"""
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RAW, http_get, merge_csv, ms_to_date, now_utc_iso, read_csv_keys, unzip_single_csv  # noqa: E402

FIELDS = ["date", "volume", "taker_buy_volume", "taker_ratio", "partial", "source", "fetched_at"]
SYMBOLS = ["XRP", "BTC"]
VISION = "https://data.binance.vision/data/spot"
LIVE = "https://data-api.binance.vision/api/v3/klines"
STATUS = os.path.join(RAW, "flows", "refflow_status.json")


def _ratio(vol, buy):
    try:
        v, b = float(vol), float(buy)
    except (TypeError, ValueError):
        return ""
    return f"{b / v:.6f}" if v > 0 else ""


def _rows_from_file(text, source, fetched_at):
    rows = []
    for r in csv.reader(io.StringIO(text)):
        if not r or not r[0].isdigit() or len(r) < 10:
            continue  # 見出し行・列が足りない行
        rows.append(dict(date=ms_to_date(r[0]), volume=r[5], taker_buy_volume=r[9],
                         taker_ratio=_ratio(r[5], r[9]), partial="0", source=source, fetched_at=fetched_at))
    return rows


def taker(sym):
    pair = f"{sym}USDT"
    path = os.path.join(RAW, "flows", f"binance_taker_{sym.lower()}_1d.csv")
    fetched_at = now_utc_iso()
    now = datetime.now(timezone.utc)
    today = now.date()
    last_month = today.replace(day=1) - timedelta(days=1)
    final = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            final = {r["date"]: r["partial"] for r in csv.DictReader(f)}
    have_months = {d[:7] for d, p in final.items() if p == "0"}
    rows, notes = [], []

    # 過去分：月次ファイル（2018-01〜先月）。持っていない月と、先月だけ取る
    y, m = 2018, 1
    while (y, m) < (today.year, today.month):
        ym = f"{y:04d}-{m:02d}"
        if ym not in have_months or (y, m) == (last_month.year, last_month.month):
            try:
                blob = http_get(f"{VISION}/monthly/klines/{pair}/1d/{pair}-1d-{ym}.zip", as_json=False)
                rows += _rows_from_file(unzip_single_csv(blob), f"data.binance.vision monthly klines {pair} 1d", fetched_at)
            except Exception:  # noqa: BLE001  上場前の月・まだ公開されていない月
                pass
        m += 1
        if m == 13:
            y, m = y + 1, 1

    # 今月の確定分：日次ファイル（昨日まで。確定済みの日は取り直さない）
    d = today.replace(day=1)
    while d < today:
        ds = d.isoformat()
        if final.get(ds) != "0":
            try:
                blob = http_get(f"{VISION}/daily/klines/{pair}/1d/{pair}-1d-{ds}.zip", as_json=False)
                rows += _rows_from_file(unzip_single_csv(blob), f"data.binance.vision daily klines {pair} 1d", fetched_at)
            except Exception:  # noqa: BLE001  まだ公開されていない（前日分は公開が少し遅れる）
                pass
        d += timedelta(days=1)

    # 当日（途中）と、日次ファイルがまだ出ていない前日：公開データ専用 API
    try:
        k = http_get(f"{LIVE}?symbol={pair}&interval=1d&limit=3")
        for r in k:
            day = ms_to_date(r[0])
            end = datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc) + timedelta(days=1)
            partial = "1" if end > now else "0"
            if partial == "0" and final.get(day) == "0":
                continue  # 確定済みは一括データの値を優先して残す
            rows.append(dict(date=day, volume=r[5], taker_buy_volume=r[9], taker_ratio=_ratio(r[5], r[9]),
                             partial=partial, source=f"Binance data-api.binance.vision /api/v3/klines {pair} 1d",
                             fetched_at=fetched_at))
    except Exception as e:  # noqa: BLE001
        notes.append(f"当日分を取れず（{type(e).__name__}: {str(e)[:80]}）。当日の行は作らない")

    total, added = merge_csv(path, rows, ["date"], FIELDS)
    with open(path, newline="", encoding="utf-8") as f:
        allrows = list(csv.DictReader(f))
    last = allrows[-1] if allrows else {}
    blanks = sum(1 for r in allrows if not r["taker_ratio"])
    return dict(ok=True, rows=total, added=added, first=allrows[0]["date"] if allrows else "",
                last=last.get("date", ""), last_partial=last.get("partial", ""),
                last_taker_ratio=last.get("taker_ratio", ""), blank_ratio_rows=blanks,
                note="; ".join(notes))


def main():
    status = {"checked_at": now_utc_iso(), "taker": {}}
    if os.path.exists(STATUS):
        try:
            with open(STATUS, encoding="utf-8") as f:
                status = {**json.load(f), "checked_at": now_utc_iso()}
        except ValueError:
            pass
    status.setdefault("taker", {})
    failed = 0
    for sym in SYMBOLS:
        try:
            r = taker(sym)
            status["taker"][sym] = r
            print(f"[OK]   taker {sym}: {r}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            prev = status["taker"].get(sym, {})
            prev.update(ok=False, last_error=f"{type(e).__name__}: {str(e)[:200]}", last_failed_at=now_utc_iso())
            status["taker"][sym] = prev
            print(f"[FAIL] taker {sym}: {e}")
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    return 1 if failed == len(SYMBOLS) else 0


if __name__ == "__main__":
    sys.exit(main())
