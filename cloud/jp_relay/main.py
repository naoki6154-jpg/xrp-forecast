"""日本（Google Cloud Run 東京リージョン）に置く中継係（設計書 3-4 方法C）。

米国のサーバー（GitHub Actions）からは取れない Binance・Bybit の公開データを、
東京から代わりに取得し、加工せずにそのまま返す。保存は GitHub Actions 側で行う。

安全のため：
- 決められた取得先（下の ENDPOINTS）だけを取りに行く。任意のURLは取らない（中継の悪用防止）。
- 合言葉（環境変数 RELAY_TOKEN）がヘッダー X-Relay-Token と一致しない要求は断る。
- 取引所の口座やAPIキーは一切使わない（公開データだけ）。
"""
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = os.environ.get("RELAY_TOKEN", "")
UA = "xrp-forecast-jp-relay/0.1"
SYMBOLS_ALLOWED = {"XRP", "BTC", "ETH"}

# 名前: URLの型（{s} は XRPUSDT などに置き換える）
ENDPOINTS = {
    # Binance 先物（USDⓈ-M）
    "binance_premium": "https://fapi.binance.com/fapi/v1/premiumIndex?symbol={s}",
    "binance_oi_now": "https://fapi.binance.com/fapi/v1/openInterest?symbol={s}",
    "binance_oi_hist_1h": "https://fapi.binance.com/futures/data/openInterestHist?symbol={s}&period=1h&limit=48",
    "binance_lsr_global_1h": "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={s}&period=1h&limit=48",
    "binance_lsr_top_pos_1h": "https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={s}&period=1h&limit=48",
    "binance_taker_ratio_1h": "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={s}&period=1h&limit=48",
    "binance_funding": "https://fapi.binance.com/fapi/v1/fundingRate?symbol={s}&limit=100",
    # Binance 現物
    "binance_spot_24h": "https://api.binance.com/api/v3/ticker/24hr?symbol={s}",
    # Bybit 先物（linear）
    "bybit_ticker": "https://api.bybit.com/v5/market/tickers?category=linear&symbol={s}",
    "bybit_oi_1h": "https://api.bybit.com/v5/market/open-interest?category=linear&symbol={s}&intervalTime=1h&limit=48",
    "bybit_lsr_1h": "https://api.bybit.com/v5/market/account-ratio?category=linear&symbol={s}&period=1h&limit=48",
    "bybit_funding": "https://api.bybit.com/v5/market/funding/history?category=linear&symbol={s}&limit=50",
}


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def fetch(name, sym):
    url = ENDPOINTS[name].format(s=f"{sym}USDT")
    item = {"name": name, "symbol": sym, "url": url, "requested_at": now_iso()}
    for i in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8")
                item.update(status=r.status, received_at=now_iso())
            try:
                item["body"] = json.loads(raw)
            except ValueError:
                item["body_text"] = raw[:2000]
            return item
        except urllib.error.HTTPError as e:
            item.update(status=e.code, received_at=now_iso(),
                        error=e.read().decode("utf-8", "replace")[:500])
            if e.code < 500:
                return item
        except Exception as e:  # noqa: BLE001
            item.update(status=0, received_at=now_iso(), error=f"{type(e).__name__}: {e}"[:500])
        time.sleep(1)
    return item


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, {"ok": True})
        if u.path != "/bundle":
            return self._send(404, {"error": "not found"})
        given = self.headers.get("X-Relay-Token", "")
        if not TOKEN or not hmac.compare_digest(given, TOKEN):
            return self._send(403, {"error": "forbidden"})
        q = parse_qs(u.query)
        syms = [s for s in q.get("symbols", ["XRP,BTC"])[0].upper().split(",") if s in SYMBOLS_ALLOWED]
        names = [n for n in q.get("names", [",".join(ENDPOINTS)])[0].split(",") if n in ENDPOINTS]
        jobs = [(n, s) for s in syms for n in names]
        with ThreadPoolExecutor(max_workers=8) as ex:
            items = list(ex.map(lambda a: fetch(*a), jobs))
        self._send(200, {"relay": "gcp-cloud-run asia-northeast1", "generated_at": now_iso(), "items": items})

    def log_message(self, fmt, *args):  # Cloud Run のログに1行だけ出す
        print(f"{self.address_string()} {fmt % args}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("", port), Handler).serve_forever()
