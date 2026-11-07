"""経済指標の発表（CPI・雇用統計・PCE）を、過去事実データベースに登録する（設計書 3-3）。

- 発表日：FRED の release/dates（一次データ。時刻は含まれない）
- 値：ALFRED（FREDの過去時点のデータ）で「発表当日に公表されていた値」を取り、前月比などを計算する。
  後から修正された値は使わない。
- 発表時刻：BLS（CPI・雇用統計）と BEA（PCE）の定例の公表時刻 8:30 ET を使う（time_basis に明記）。
- 市場予想（consensus）は、無料の一次データの取得先がまだないため空欄。推測で埋めない。

出力：facts/events.csv（出来事1件1行）と facts/event_values.csv（出来事ごとの数値）。
すでに登録済みの出来事は取り直さない（毎日の実行では新しい発表だけ足す）。
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "fetchers"))
from common import merge_csv, now_utc_iso  # noqa: E402

FACTS = os.path.join(ROOT, "facts")
ET = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")
START = date(2016, 1, 1)
RECOMPUTE_DAYS = int(os.environ.get("FACTS_RECOMPUTE_DAYS", "460"))  # 直近の分は毎回計算し直す（後から直した計算方法を反映するため）
MAX_SECONDS = int(os.environ.get("FACTS_MAX_SECONDS", "1200"))  # 1回の実行の上限。残りは次の回に続ける
API = "https://api.stlouisfed.org/fred"

EVENT_FIELDS = ["event_id", "category", "subcategory", "name", "date_et", "datetime_utc", "datetime_jst",
                "time_basis", "reference_period", "details", "source", "source_url", "recorded_at"]
VALUE_FIELDS = ["event_id", "metric", "value", "previous", "consensus", "surprise", "unit", "method", "source"]

# release_id: (名前, 種類, [(指標名, 系列ID, 計算, 単位)])
RELEASES = {
    10: ("米CPI（消費者物価指数）", "cpi", [
        ("CPI 前月比", "CPIAUCSL", "mom", "%"),
        ("CPI 前年比", "CPIAUCNS", "yoy", "%"),
        ("コアCPI 前月比", "CPILFESL", "mom", "%"),
        ("コアCPI 前年比", "CPILFENS", "yoy", "%"),
    ]),
    50: ("米雇用統計", "nfp", [
        ("非農業部門雇用者数 前月差", "PAYEMS", "diff", "千人"),
        ("失業率", "UNRATE", "level", "%"),
    ]),
    54: ("米PCE（個人消費支出）物価指数", "pce", [
        ("PCE 前月比", "PCEPI", "mom", "%"),
        ("PCE 前年比", "PCEPI", "yoy", "%"),
        ("コアPCE 前月比", "PCEPILFE", "mom", "%"),
        ("コアPCE 前年比", "PCEPILFE", "yoy", "%"),
    ]),
}


def get(url, key):
    for i in range(4):
        try:
            req = urllib.request.Request(url.replace("KEY", key), headers={"User-Agent": "xrp-forecast-facts"})
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(0.6)  # FRED の回数制限（毎分120回）に余裕を持たせる
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"FRED取得に失敗: {url.split('?')[0]}")


def vintage(series, d, key):
    """日付 d の時点で公表されていた系列（直近26ヶ月分）"""
    start = (d - timedelta(days=800)).isoformat()
    j = get(f"{API}/series/observations?series_id={series}&api_key=KEY&file_type=json"
            f"&realtime_start={d}&realtime_end={d}&observation_start={start}", key)
    return [(o["date"], float(o["value"])) for o in j["observations"] if o["value"] != "."]


def _shift(ym, months):
    y, m = int(ym[:4]), int(ym[5:7])
    m += months
    while m <= 0:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def calc(obs, how, idx):
    """obs の idx 番目（-1=最新、-2=その1ヶ月前）の月の値を計算する。
    前月・前年同月は「日付」で探す（公表されなかった月があっても、ずれないように。例：2025年10月のCPI）。
    比べる月の値がなければ空欄。"""
    if not obs:
        return None
    by = {d[:7]: v for d, v in obs}
    ym = _shift(obs[-1][0][:7], idx + 1)
    cur = by.get(ym)
    if cur is None:
        return None
    if how == "level":
        return cur
    base = by.get(_shift(ym, -12 if how == "yoy" else -1))
    if base is None:
        return None
    if how == "diff":
        return cur - base
    return (cur / base - 1) * 100


def fmt(v, how):
    if v is None:
        return ""
    return f"{v:.0f}" if how == "diff" else f"{v:.2f}"


def main():
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        sys.exit("FRED_API_KEY が未設定")
    ev_path = os.path.join(FACTS, "events.csv")
    have, refs_seen = set(), set()
    if os.path.exists(ev_path):
        with open(ev_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["category"] != "経済指標":
                    continue
                if date.fromisoformat(r["date_et"]) >= date.today() - timedelta(days=RECOMPUTE_DAYS):
                    continue  # 計算し直す対象
                have.add(r["event_id"])
                refs_seen.add((r["subcategory"], r["reference_period"]))
    today = datetime.now(timezone.utc).date()
    recorded_at = now_utc_iso()
    events, values = [], []
    t_start = time.time()
    run_refs = {}
    stopped = False
    for rid, (title, sub, metrics) in RELEASES.items():
        j = get(f"{API}/release/dates?release_id={rid}&api_key=KEY&file_type=json&realtime_start={START}"
                f"&realtime_end=9999-12-31&include_release_dates_with_no_data=false&sort_order=asc&limit=10000", key)
        dates = [date.fromisoformat(x["date"]) for x in j["release_dates"]]
        dates = [d for d in dates if START <= d < today]
        # 新しい発表から順に取り込む（直近の分を先にそろえる）
        new = sorted([d for d in dates if f"{sub}-{d}" not in have], reverse=True)
        print(f"{title}: 発表日 {len(dates)}件（新規 {len(new)}件）")
        last_ref = None
        for d in new:
            if time.time() - t_start > MAX_SECONDS:
                stopped = True
                break
            series_cache = {}
            ref = None
            row_values = []
            for mname, sid, how, unit in metrics:
                if sid not in series_cache:
                    series_cache[sid] = vintage(sid, d, key)
                obs = series_cache[sid]
                if not obs:
                    continue
                ref = ref or obs[-1][0][:7]
                row_values.append(dict(event_id=f"{sub}-{d}", metric=mname, value=fmt(calc(obs, how, -1), how),
                                       previous=fmt(calc(obs, how, -2), how), consensus="", surprise="",
                                       unit=unit,
                                       method=f"{sid} の {d} 時点の公表値（ALFRED）から計算。対象月 {obs[-1][0][:7]}"
                                              + ("。公表値は指数の丸めにより0.1ポイント違うことがある" if how in ("mom", "yoy") else ""),
                                       source="FRED/ALFRED API series/observations（realtime_start=realtime_end=発表日）"))
            if not row_values:
                continue
            if (sub, ref) in refs_seen:
                # 同じ対象月が2回出た：一番早い日を初回発表として残す（新しい順に処理しているので、前に入れた方を消す）
                prev_id = run_refs.get((sub, ref))
                if prev_id is None:
                    continue  # 前の回で登録済み（そちらの方が早い）
                events = [e for e in events if e["event_id"] != prev_id]
                values = [v for v in values if v["event_id"] != prev_id]
            refs_seen.add((sub, ref))
            run_refs[(sub, ref)] = f"{sub}-{d}"
            t_et = datetime(d.year, d.month, d.day, 8, 30, tzinfo=ET)
            events.append(dict(
                event_id=f"{sub}-{d}", category="経済指標", subcategory=sub, name=f"{title}（{ref}分）",
                date_et=d.isoformat(),
                datetime_utc=t_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                datetime_jst=t_et.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                time_basis="定例の公表時刻 8:30 ET（BLS・BEAの慣例。個別の時刻は未確認）",
                reference_period=ref, details="",
                source=f"FRED API release/dates release_id={rid}",
                source_url=f"https://fred.stlouisfed.org/releases/{rid}", recorded_at=recorded_at))
            values += row_values
            last_ref = ref
        print(f"  一番古く登録した対象月：{last_ref}")
    merge_csv(ev_path, events, ["event_id"], EVENT_FIELDS)
    merge_csv(os.path.join(FACTS, "event_values.csv"), values, ["event_id", "metric"], VALUE_FIELDS)
    print(f"登録：出来事 {len(events)}件、数値 {len(values)}件" + ("（時間の上限で区切った。続きは次の回）" if stopped else ""))


if __name__ == "__main__":
    main()
