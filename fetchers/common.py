"""取得プログラムの共通部品。

ルール（設計書 3-2）：
- 全ての記録に「取得元・取得日時・加工前の値」を残す。
- 取れなかった値は空欄にし、推測で埋めない。
- 標準ライブラリだけで動かす（GitHub Actions で追加インストール不要）。
"""
import csv
import io
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
JST = timezone(timedelta(hours=9))
UA = "xrp-forecast-fetcher/0.1 (+https://github.com/naoki6154-jpg/xrp-forecast)"


def now_utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_get(url, timeout=30, retries=3, as_json=True):
    """URLを取得する。失敗したら少し待って再試行する。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:  # 存在しないファイル（まだ公開されていない月など）
                raise
            time.sleep(2 * (i + 1))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def ms_to_date(ms):
    """ミリ秒（またはマイクロ秒）のUNIX時刻 → UTCの日付文字列"""
    ms = int(ms)
    if ms > 10**14:  # マイクロ秒
        ms //= 1000
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def ms_to_iso(ms):
    ms = int(ms)
    if ms > 10**14:
        ms //= 1000
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def merge_csv(path, rows, key_fields, fieldnames):
    """既存のCSVに行を足す。同じキーの行は新しい取得で上書きし、キー順に並べる。

    戻り値：(全体の行数, 新しく増えた行数)
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[tuple(r[k] for k in key_fields)] = r
    before = len(existing)
    for r in rows:
        existing[tuple(str(r[k]) for k in key_fields)] = {k: r.get(k, "") for k in fieldnames}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for k in sorted(existing):
            w.writerow(existing[k])
    return len(existing), len(existing) - before


def read_csv_keys(path, field):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {r[field] for r in csv.DictReader(f)}


def last_value(path, field):
    if not os.path.exists(path):
        return ""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-1][field] if rows else ""


def unzip_single_csv(blob):
    import zipfile

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        return z.read(name).decode("utf-8")
