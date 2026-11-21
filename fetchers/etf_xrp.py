"""米国上場の現物XRP ETFの日次の資金の流れ（司令塔の依頼 2026-11-21）。

取得元：SoSoValue の公開API（https://openapi.sosovalue.com/openapi/v1、ヘッダー x-soso-api-key）
- GET /etfs/summary-history?symbol=XRP&country_code=US … 日ごとの合計（純流入・純資産・累計純流入）
- GET /etfs?symbol=XRP&country_code=US                 … 銘柄の一覧（取れなければ既知の銘柄で代用）
- GET /etfs/{ticker}/history                            … 銘柄ごとの日ごとの純流入
SoSoValue の仕様上、期間の指定は直近1か月まで。最新の行は確定前で null のことが多い（null の行は使わない）。

出力
- data/raw/flows/etf_flows_xrp.csv：date, total_usd_m, <銘柄>（百万ドル）, cum_inflow_usd_b, net_assets_usd_b, source, fetched_at
  日付は米国の営業日。取れない日・銘柄は空欄（推測で埋めない）。
- data/raw/flows/etf_xrp_sosovalue_raw.csv：加工前の値（ドル）を銘柄・日ごとに
- data/raw/flows/etf_xrp_status.json：状況（state/latest.json への反映は state_update.py が行う）
キー（Secrets の SOSOVALUE_API_KEY）がないときは「未設定」と記録して正常に終わる。
"""
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RAW, UA, merge_csv, merge_csv_dynamic, now_utc_iso  # noqa: E402

BASE = "https://openapi.sosovalue.com/openapi/v1"
OUT = os.path.join(RAW, "flows", "etf_flows_xrp.csv")
RAWF = os.path.join(RAW, "flows", "etf_xrp_sosovalue_raw.csv")
STATUS = os.path.join(RAW, "flows", "etf_xrp_status.json")
KNOWN = ["XRP", "XRPC", "XRPZ", "GXRP", "TOXR"]  # 一覧が取れないときの代用（Bitwise・Canary・Franklin・Grayscale・21Shares）
SRC = "SoSoValue API v1（米国上場の現物XRP ETF）"


def api(path, params=None):
    key = os.environ.get("SOSOVALUE_API_KEY", "").strip()
    url = f"{BASE}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"x-soso-api-key": key, "User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    # 返り方の違い（{"code":0,"data":[...]} か、配列そのまま）に対応
    if isinstance(body, dict):
        if body.get("code") not in (None, 0, "0"):
            raise RuntimeError(f"{path}: code={body.get('code')} {str(body.get('message') or body.get('msg'))[:120]}")
        body = body.get("data", body)
        if isinstance(body, dict) and "list" in body:
            body = body["list"]
    return body


def _num(x):
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def tickers():
    try:
        lst = api("/etfs", {"symbol": "XRP", "country_code": "US"})
        got = [x.get("ticker") for x in lst if isinstance(x, dict) and x.get("ticker")]
        if got:
            return sorted(set(got)), "API の一覧"
    except Exception as e:  # noqa: BLE001
        return KNOWN, f"一覧が取れず既知の銘柄で代用（{type(e).__name__}: {str(e)[:80]}）"
    return KNOWN, "一覧が空のため既知の銘柄で代用"


def main():
    fetched_at = now_utc_iso()
    status = dict(checked_at=fetched_at)
    if not os.environ.get("SOSOVALUE_API_KEY", "").strip():
        status.update(ok=False, note="SoSoValue の APIキーが未設定（GitHub の Secrets に SOSOVALUE_API_KEY を登録すると取得が始まる）")
        _save_status(status)
        print("[未設定] " + status["note"])
        return 0
    try:
        # 合計
        summ = list(api("/etfs/summary-history", {"symbol": "XRP", "country_code": "US", "limit": 300}))
        if os.environ.get("BACKFILL") == "1":
            # 遡り取得の試し：月ごとの期間指定（仕様上は直近1か月のみ。取れた分だけ使う）
            import datetime as _dt
            d0 = _dt.date(2025, 11, 1)
            while d0 < _dt.date.today() - _dt.timedelta(days=31):
                d1 = min(d0 + _dt.timedelta(days=29), _dt.date.today())
                try:
                    got = api("/etfs/summary-history", {"symbol": "XRP", "country_code": "US", "limit": 300,
                                                        "start_date": d0.isoformat(), "end_date": d1.isoformat()})
                    print(f"[遡り] {d0}〜{d1}: {len(got)}行")
                    summ += list(got)
                except Exception as e:  # noqa: BLE001
                    print(f"[遡り] {d0}〜{d1}: 失敗 {str(e)[:150]}")
                d0 = d1 + _dt.timedelta(days=1)
        totals = {}
        for x in summ:
            if _num(x.get("total_net_inflow")) is None:
                continue  # 確定前
            totals[x["date"]] = x
        # 銘柄ごと
        tks, tk_note = tickers()
        per, raw_rows, tk_errors = {}, [], []
        for t in tks:
            try:
                hist = api(f"/etfs/{urllib.parse.quote(t)}/history")
            except Exception as e:  # noqa: BLE001
                tk_errors.append(f"{t}: {type(e).__name__}")
                continue
            for x in hist:
                ni = _num(x.get("net_inflow"))
                if ni is None:
                    continue  # 確定前
                d = x["date"]
                per.setdefault(d, {})[t] = ni
                raw_rows.append(dict(date=d, ticker=t, net_inflow_usd=x.get("net_inflow"), cum_inflow_usd=x.get("cum_inflow"),
                                     net_assets_usd=x.get("net_assets"), value_traded_usd=x.get("value_traded"),
                                     prem_dsc=x.get("prem_dsc"), source=f"{SRC} /etfs/{t}/history", fetched_at=fetched_at))
        merge_csv(RAWF, raw_rows, ["date", "ticker"],
                  ["date", "ticker", "net_inflow_usd", "cum_inflow_usd", "net_assets_usd", "value_traded_usd", "prem_dsc",
                   "source", "fetched_at"])
        rows = []
        for d in sorted(set(totals) | set(per)):
            t = totals.get(d, {})
            row = dict(date=d, total_usd_m=(f"{_num(t['total_net_inflow']) / 1e6:.4f}" if t else ""))
            for tk in tks:
                v = per.get(d, {}).get(tk)
                row[tk] = "" if v is None else f"{v / 1e6:.4f}"
            cum, na = _num(t.get("cum_net_inflow")), _num(t.get("total_net_assets"))
            row.update(cum_inflow_usd_b="" if cum is None else f"{cum / 1e9:.4f}",
                       net_assets_usd_b="" if na is None else f"{na / 1e9:.4f}",
                       source=f"{SRC}：合計は /etfs/summary-history、銘柄は /etfs/{{ticker}}/history（{tk_note}）",
                       fetched_at=fetched_at)
            rows.append(row)
        total, added = merge_csv_dynamic(OUT, rows, ["date"], first_fields=["date", "total_usd_m"])
        # 列の順番を依頼どおりに（date, total_usd_m, 銘柄…, cum_inflow_usd_b, net_assets_usd_b, source, fetched_at）
        _reorder(OUT)
        last = max(totals) if totals else ""
        status.update(ok=True, rows=total, added=added, last=last, tickers=tks, ticker_note=tk_note,
                      ticker_errors=tk_errors, first_in_this_fetch=min(totals) if totals else "",
                      last_total_usd_m=rows[-1]["total_usd_m"] if rows else "")
        print(f"[OK]   XRP ETF: {total}行（今回 {len(rows)}日分、最新の確定日 {last}）。銘柄 {tks}（{tk_note}）"
              + (f" 取れなかった銘柄 {tk_errors}" if tk_errors else ""))
        code = 0
    except Exception as e:  # noqa: BLE001
        status.update(ok=False, error=f"{type(e).__name__}: {str(e)[:300]}")
        print(f"[FAIL] XRP ETF: {e}")
        code = 1
    _save_status(status)
    return code


def _reorder(path):
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        rows, fields = list(rd), rd.fieldnames
    head = ["date", "total_usd_m"]
    tail = ["cum_inflow_usd_b", "net_assets_usd_b", "source", "fetched_at"]
    mid = [c for c in fields if c not in head + tail]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=head + mid + tail, restval="")
        w.writeheader()
        w.writerows(rows)


def _save_status(status):
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    prev = {}
    if os.path.exists(STATUS):
        try:
            with open(STATUS, encoding="utf-8") as f:
                prev = json.load(f)
        except ValueError:
            pass
    if not status.get("ok") and prev.get("ok"):
        # 失敗した回は、前回成功時の情報を残す（state の点検で「古くなった」と分かるように）
        status = {**prev, "ok": False, "last_error": status.get("error") or status.get("note"),
                  "last_failed_at": status["checked_at"]}
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
