"""価格だけの更新（ダッシュボードの「価格を更新」ボタン／毎時の軽い作業用）。予想は計算しない。

  python3 engine/live_price.py        config/livePrice に書く内容（db_write の ops 1件分の data）を表示する

保存済みの最新の取得（data/raw、毎時5分に GitHub Actions が更新）から現在値を読むだけ。
円は bitbank、ドルは Binance 現物（東京経由）。古い・取れないときは出力の stale や source に出る。
ダッシュボードは、予想の計算時点より新しいときだけ、この値を現在値として表示する（livePx）。
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import inputs as I

cfg = json.load(open(os.path.join(HERE, "config.json")))
now = datetime.now(timezone.utc)
usd, jpy, fx, fb = I.current_prices(ROOT, now, cfg)
at = max([x for x in (jpy.get("at"), usd.get("at")) if x is not None])
JST = timezone(timedelta(hours=9))
out = {
    "price_jpy": round(jpy["price"], 2), "price_usd": round(usd["price"], 4),
    "at": at.astimezone(JST).isoformat(timespec="seconds"), "at_ms": int(at.timestamp() * 1000),
    "source": jpy["source"] + " / " + usd["source"], "jpy_primary": bool(jpy.get("primary")),
    "chg_prev_close": jpy.get("chg_prev_close"), "fx": round(fx["rate"], 3) if fx.get("rate") else None,
    "fallbacks": [f["used"] for f in fb], "written_at": now.astimezone(JST).isoformat(timespec="seconds"),
}
print(json.dumps(out, ensure_ascii=False))
