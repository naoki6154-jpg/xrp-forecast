"""アルトコイン季節指数（自前・Binance売買代金上位50）。設計書 追補 第18章 ③、計算方法は docs/altseason.md。

手順
1. 銘柄の一覧：Binance 公式一括データ（data.binance.vision）の S3 の一覧から、USDT 建ての現物の銘柄
   （上場廃止の銘柄を含む）。ステーブルコイン・法定通貨・金・ラップトークン・レバレッジトークンを除く。
2. 日足：月次ファイル（過去分）と、公開データ専用 API（data-api.binance.vision、直近分）。
   加工前の値（終値・売買代金）を data/raw/universe/YYYY-MM.csv に月ごとに保存する。
3. 計算：毎日、90日前の終値と直近30日の売買代金がある銘柄のうち、直近30日の売買代金の上位50を選び、
   90日の騰落率が BTC を上回った割合（0〜100）。対象が20銘柄未満の日は空欄。当日（途中）は計算しない。
出力：data/raw/sentiment/altseason.csv
  列：date, value, n_coins, n_beat, btc_ret_90d_pct, source, fetched_at
付随：data/clean/altseason_members_latest.csv（最新日の上位50の内訳）、data/raw/sentiment/altseason_status.json
1回の実行の上限時間を超えたら、そこまでを保存して終わり、続きは次の回に取る。
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, RAW, UA, http_get, ms_to_date, now_utc_iso, unzip_single_csv  # noqa: E402

UNI = os.path.join(RAW, "universe")
OUT = os.path.join(RAW, "sentiment", "altseason.csv")
STATUS = os.path.join(RAW, "sentiment", "altseason_status.json")
MEMBERS = os.path.join(ROOT, "data", "clean", "altseason_members_latest.csv")
DONE = os.path.join(UNI, "_months_done.csv")  # 取り込み済みの（銘柄, 月）。月次ファイルは公開後に変わらない
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
VISION = "https://data.binance.vision/data/spot"
LIVE = "https://data-api.binance.vision/api/v3"
MAX_SECONDS = int(os.environ.get("ALTSEASON_MAX_SECONDS", "2400"))
TOP_N, WINDOW, VOL_DAYS, MIN_COINS = 50, 90, 30, 20

STABLE_FIAT_GOLD = {
    "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "PAX", "UST", "USTC", "USDS", "USDSB", "SUSD", "USDE", "USD1",
    "PYUSD", "RLUSD", "AEUR", "EURI", "XUSD", "VAI", "BKRW", "BIDR", "IDRT", "BVND", "EUR", "GBP", "AUD", "TRY",
    "BRL", "RUB", "UAH", "NGN", "ZAR", "PLN", "RON", "ARS", "JPY", "MXN", "COP", "CZK", "PAXG", "XAUT",
}
WRAPPED = {"WBTC", "WBETH", "BETH", "WETH", "CBETH", "STETH", "WSTETH", "BTCB"}


def s3_list(prefix, delimiter="/"):
    """S3 の一覧（1000件ずつ）。CommonPrefixes と Key を返す"""
    prefixes, keys, marker = [], [], ""
    while True:
        url = f"{S3}?delimiter={delimiter}&prefix={prefix}" + (f"&marker={marker}" if marker else "")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            xml = r.read().decode("utf-8")
        p = re.findall(r"<Prefix>([^<]+)</Prefix>", xml)[1:]  # 先頭は問い合わせた prefix 自身
        k = re.findall(r"<Key>([^<]+)</Key>", xml)
        prefixes += p
        keys += k
        if "<IsTruncated>true</IsTruncated>" not in xml:
            return prefixes, keys
        nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", xml)
        marker = nm.group(1) if nm else (k[-1] if k else p[-1])


def universe_symbols():
    prefixes, _ = s3_list("data/spot/monthly/klines/")
    pairs = [p.rstrip("/").split("/")[-1] for p in prefixes]
    usdt = [s for s in pairs if s.endswith("USDT") and len(s) > 4]
    bases = {s[:-4] for s in usdt}
    out = []
    for s in usdt:
        b = s[:-4]
        if b in STABLE_FIAT_GOLD or b in WRAPPED:
            continue
        lev = next((suf for suf in ("UP", "DOWN", "BULL", "BEAR") if b.endswith(suf) and b[: -len(suf)] in bases), None)
        if lev:
            continue  # レバレッジトークン（例：BTCUP、BNBBULL）
        out.append(s)
    return sorted(out)


def load_done():
    if not os.path.exists(DONE):
        return set()
    with open(DONE, newline="", encoding="utf-8") as f:
        return {(r["symbol"], r["month"]) for r in csv.DictReader(f)}


def save_month_rows(rows):
    """rows: [(date, symbol, close, quote_volume, source, fetched_at)] を月ごとのファイルに足す（同じ日・銘柄は上書き）"""
    by_month = {}
    for r in rows:
        by_month.setdefault(r[0][:7], []).append(r)
    os.makedirs(UNI, exist_ok=True)
    for ym, rs in by_month.items():
        p = os.path.join(UNI, f"{ym}.csv")
        cur = {}
        if os.path.exists(p):
            with open(p, newline="", encoding="utf-8") as f:
                for x in csv.DictReader(f):
                    cur[(x["date"], x["symbol"])] = x
        for d, s, c, q, src, fa in rs:
            cur[(d, s)] = dict(date=d, symbol=s, close=c, quote_volume=q, source=src, fetched_at=fa)
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["date", "symbol", "close", "quote_volume", "source", "fetched_at"])
            w.writeheader()
            for k in sorted(cur):
                w.writerow(cur[k])


def fetch_month(sym, ym, fetched_at):
    blob = http_get(f"{VISION}/monthly/klines/{sym}/1d/{sym}-1d-{ym}.zip", as_json=False, retries=2)
    rows = []
    for r in csv.reader(io.StringIO(unzip_single_csv(blob))):
        if r and r[0].isdigit():
            rows.append((ms_to_date(r[0]), sym, r[4], r[7], "data.binance.vision monthly klines 1d", fetched_at))
    return rows


def backfill(symbols, t0):
    """月次ファイルの取り込み（まだの（銘柄, 月）だけ）。時間の上限で区切る"""
    done = load_done()
    today = datetime.now(timezone.utc).date()
    last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    todo = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        listings = dict(zip(symbols, ex.map(lambda s: s3_list(f"data/spot/monthly/klines/{s}/1d/", "")[1], symbols)))
    for s, keys in listings.items():
        for k in keys:
            m = re.search(rf"{s}-1d-(\d{{4}}-\d{{2}})\.zip$", k)
            if m and m.group(1) >= "2017-10" and (s, m.group(1)) not in done:
                todo.append((s, m.group(1)))
    fetched_at = now_utc_iso()
    got_rows, finished, stopped = [], [], False
    batch = 400
    for i in range(0, len(todo), batch):
        if time.time() - t0 > MAX_SECONDS:
            stopped = True
            break
        part = todo[i:i + batch]
        with ThreadPoolExecutor(max_workers=16) as ex:
            res = list(ex.map(lambda a: _safe(fetch_month, *a, fetched_at), part))
        for (s, ym), rows in zip(part, res):
            if rows is not None:
                got_rows += rows
                if ym <= last_month:
                    finished.append((s, ym))
        save_month_rows(got_rows)
        got_rows = []
        with open(DONE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["symbol", "month"])
            # 先月分は、月の途中で更新されることがないよう、来月に入ってから確定とする（公開は月初）
            for s, ym in finished:
                w.writerow([s, ym])
        finished = []
    return len(todo), stopped


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001  （存在しない月など）
        return None


def recent(fetched_at, universe):
    """直近（最大60日）：取引中の USDT 銘柄の日足を公開データ専用 API から"""
    info = http_get(f"{LIVE}/exchangeInfo?permissions=SPOT", retries=2)
    trading = [x["symbol"] for x in info["symbols"] if x.get("status") == "TRADING" and x.get("quoteAsset") == "USDT"]
    allowed = set(universe)
    syms = [s for s in trading if s in allowed]
    now = datetime.now(timezone.utc)

    def one(s):
        k = http_get(f"{LIVE}/klines?symbol={s}&interval=1d&limit=60", retries=2)
        rows = []
        for r in k:
            end = datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc) + timedelta(days=1)
            if end <= now:  # 確定した日だけ
                rows.append((ms_to_date(r[0]), s, r[4], r[7], "data-api.binance.vision /api/v3/klines 1d", fetched_at))
        return rows

    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(lambda s: _safe(one, s), syms):
            rows += res or []
    save_month_rows(rows)
    return len(syms)


def compute(fetched_at):
    import pandas as pd

    frames = [pd.read_csv(os.path.join(UNI, f), dtype={"close": float, "quote_volume": float})
              for f in sorted(os.listdir(UNI)) if re.match(r"\d{4}-\d{2}\.csv$", f)]
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    close = df.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").asfreq("D")
    qv = df.pivot_table(index="date", columns="symbol", values="quote_volume", aggfunc="last").asfreq("D")
    ret = close / close.shift(WINDOW) - 1
    qv30 = qv.rolling(VOL_DAYS, min_periods=VOL_DAYS - 5).sum()
    btc = ret["BTCUSDT"]
    alts = [c for c in close.columns if c != "BTCUSDT"]
    rows, latest = [], None
    for d in close.index:
        b = btc.get(d)
        if pd.isna(b):
            continue
        r_d, v_d = ret.loc[d, alts], qv30.loc[d, alts]
        ok = r_d.notna() & v_d.notna() & close.loc[d, alts].notna()
        top = v_d[ok].sort_values(ascending=False).head(TOP_N)
        n = len(top)
        beat = int((r_d[top.index] > b).sum())
        val = f"{beat / n * 100:.1f}" if n >= MIN_COINS else ""
        rows.append(dict(date=d.strftime("%Y-%m-%d"), value=val, n_coins=n, n_beat=beat if val else "",
                         btc_ret_90d_pct=f"{b * 100:.2f}",
                         source="自前計算（Binance USDT現物の売買代金上位50、90日騰落率がBTCを上回った割合。docs/altseason.md）",
                         fetched_at=fetched_at))
        latest = (d, top, r_d, b)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "value", "n_coins", "n_beat", "btc_ret_90d_pct", "source", "fetched_at"])
        w.writeheader()
        w.writerows(rows)
    if latest:
        d, top, r_d, b = latest
        os.makedirs(os.path.dirname(MEMBERS), exist_ok=True)
        with open(MEMBERS, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "rank", "symbol", "quote_volume_30d_usdt", "ret_90d_pct", "btc_ret_90d_pct", "beat_btc"])
            for i, (s, v) in enumerate(top.items(), 1):
                w.writerow([d.strftime("%Y-%m-%d"), i, s, f"{v:.0f}", f"{r_d[s] * 100:.2f}", f"{b * 100:.2f}",
                            int(r_d[s] > b)])
    return rows


def main():
    t0 = time.time()
    fetched_at = now_utc_iso()
    status = dict(checked_at=fetched_at)
    try:
        syms = sorted(set(universe_symbols()) | {"BTCUSDT"})
        status["symbols_in_universe"] = len(syms) - 1
        todo, stopped = backfill(syms, t0)
        status.update(months_todo_at_start=todo, backfill_stopped_by_time=stopped)
        status["recent_symbols"] = recent(fetched_at, syms)
        if stopped:
            # 過去分がそろうまでは計算しない（途中のデータで計算すると、値が実際と違ってしまうため）
            status.update(ok=True, rows=0, note="過去分の取り込みが途中のため、まだ計算していない（次の回に続ける）")
            print("[途中] 過去分の取り込みを時間で区切った。altseason.csv は次の回以降に作る")
            raise StopIteration
        rows = compute(fetched_at)
        vals = [r for r in rows if r["value"]]
        status.update(ok=True, rows=len(rows), first_valued=vals[0]["date"] if vals else "",
                      last=vals[-1] if vals else {}, note=("過去分の取り込みが途中（次の回に続ける）" if stopped else ""))
        print(f"[OK] 季節指数 {len(rows)}日（値あり {len(vals)}日）。最新 {vals[-1] if vals else '-'}"
              + ("（過去分は途中）" if stopped else ""))
        code = 0
    except StopIteration:
        code = 0
    except Exception as e:  # noqa: BLE001
        status.update(ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")
        print(f"[FAIL] 季節指数: {e}")
        code = 1
    status["seconds"] = round(time.time() - t0)
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    return code


if __name__ == "__main__":
    sys.exit(main())
