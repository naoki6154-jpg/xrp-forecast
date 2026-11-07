"""全ての取得先を順に実行し、結果を state/latest.json と data/raw/_fetch_log.csv に記録する。

使い方： python3 fetchers/run.py            （全部）
        python3 fetchers/run.py fred okx   （指定したものだけ）
1つの取得先が失敗しても、他は続ける。
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, RAW, merge_csv, now_utc_iso  # noqa: E402
from sources import ALL  # noqa: E402


def main(names):
    started = now_utc_iso()
    results = {}
    for name in names:
        try:
            r = ALL[name]()
            results[name] = dict(ok=True, fetched_at=now_utc_iso(), error="", **r)
            print(f"[OK]   {name}: {r}")
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            # APIキーがエラー文に混ざらないようにする
            key = os.environ.get("FRED_API_KEY", "")
            if key:
                msg = msg.replace(key, "***")
            results[name] = dict(ok=False, fetched_at=now_utc_iso(), error=msg[:300])
            print(f"[FAIL] {name}: {msg}")
            traceback.print_exc()

    # 状態ファイルの更新（前回の値に上書きで足す）
    state_path = os.path.join(ROOT, "state", "latest.json")
    state = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    state.setdefault("sources", {})
    for name, r in results.items():
        prev = state["sources"].get(name, {})
        if r["ok"]:
            state["sources"][name] = r
        else:
            # 失敗した回は、前回成功時の情報を残し、失敗の記録を足す
            prev.update(ok=False, last_error=r["error"], last_failed_at=r["fetched_at"])
            state["sources"][name] = prev
    state["last_fetch_run"] = started
    state["updated_at"] = now_utc_iso()
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # 取得の記録（1回の実行ごと・取得先ごとに1行）
    log_rows = [dict(run_at=started, source=n, ok=int(r["ok"]), rows=r.get("rows", ""),
                     added=r.get("added", ""), last=r.get("last", ""), error=r.get("error", ""))
                for n, r in results.items()]
    merge_csv(os.path.join(RAW, "_fetch_log.csv"), log_rows, ["run_at", "source"],
              ["run_at", "source", "ok", "rows", "added", "last", "error"])

    failed = [n for n, r in results.items() if not r["ok"]]
    print(f"完了：成功 {len(results) - len(failed)} / 失敗 {len(failed)} {failed}")
    # 全部失敗したときだけ異常終了（通知のため）
    return 1 if failed and len(failed) == len(results) else 0


if __name__ == "__main__":
    args = sys.argv[1:] or list(ALL)
    sys.exit(main(args))
