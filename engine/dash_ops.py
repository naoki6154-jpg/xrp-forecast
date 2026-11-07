"""ダッシュボードのデータベースに書く内容（db_write の ops）を作って表示する。AIは表示された JSON をそのまま書き写すだけ。

  python3 engine/dash_ops.py runlog --agent main --phase start --run-id R --mode morning
  python3 engine/dash_ops.py runlog --agent main --phase end --run-id R --mode morning --status 成功 --duration 95 --ok-items 4 [--failed a,b] [--note ...]
  python3 engine/dash_ops.py numeric-check --run-id R --mode morning     ①数値データ担当の点検（取得先ごとの成否と新しさ）。start と end の2件を出す
  python3 engine/dash_ops.py update                                       予想の1件分（engine/out/update.json）

書く場所（全て新しい文書なので ifVersion は不要）：
  runs/<run_id>_<agent>_<phase>   稼働記録
  updates/<run_id>                予想と各欄のデータ（1回の更新で1件）
"""
import argparse, json, os, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JST = timezone(timedelta(hours=9))
SLOTS = ["07:40", "21:20"]


def now():
    return datetime.now(JST).replace(microsecond=0)


def next_slot(t):
    for d in range(0, 2):
        for s in SLOTS:
            h, m = map(int, s.split(":"))
            c = (t + timedelta(days=d)).replace(hour=h, minute=m, second=0)
            if c > t + timedelta(minutes=5):
                return c.isoformat()
    return None


def runlog(agent, phase, run_id, mode, status=None, duration=None, ok_items=None, failed=None, note=None, at=None):
    t = at or now()
    data = {"agent": agent, "phase": phase, "run_id": run_id, "mode": mode, "at": t.isoformat(), "at_ms": int(t.timestamp() * 1000),
            "status": status or ("実行中" if phase == "start" else "成功"), "next_at": next_slot(t)}
    if phase == "end":
        data.update({"duration_sec": duration, "ok_items": ok_items, "failed_items": failed or [], "note": note})
    return {"op": "set", "path": "runs/%s_%s_%s" % (run_id, agent, phase), "data": data}


def numeric_check(run_id, mode):
    t0 = now()
    st = json.load(open(os.path.join(ROOT, "state/latest.json")))
    ok, bad = 0, []
    for k, v in st.get("sources", {}).items():
        fa = v.get("fetched_at")
        age = None
        if fa:
            age = (t0 - datetime.fromisoformat(fa.replace("Z", "+00:00"))).total_seconds() / 3600
        hourly = k in ("jp_relay",)
        limit = 2.5 if hourly else 14
        if v.get("ok") and age is not None and age <= limit:
            ok += 1
        else:
            bad.append(k + ("（失敗）" if not v.get("ok") else "（%.0f時間前の値）" % age if age is not None else "（記録なし）"))
    status = "成功" if not bad else ("一部失敗" if ok else "失敗")
    note = "GitHub Actions・東京のCloud Runが保存したデータの点検（取得そのものはクラウド側の定時実行）"
    return [runlog("numeric", "start", run_id, mode, at=t0),
            runlog("numeric", "end", run_id, mode, status=status, duration=(now() - t0).total_seconds() + 1, ok_items=ok, failed=bad, note=note,
                   at=max(now(), t0 + timedelta(seconds=1)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["runlog", "numeric-check", "update"])
    ap.add_argument("--agent"); ap.add_argument("--phase"); ap.add_argument("--run-id"); ap.add_argument("--mode", default="morning")
    ap.add_argument("--status"); ap.add_argument("--duration", type=float); ap.add_argument("--ok-items", type=int)
    ap.add_argument("--failed", default=""); ap.add_argument("--note")
    a = ap.parse_args()
    if a.cmd == "runlog":
        ops = [runlog(a.agent, a.phase, a.run_id, a.mode, a.status, a.duration, a.ok_items, [x for x in a.failed.split(",") if x], a.note)]
    elif a.cmd == "numeric-check":
        ops = numeric_check(a.run_id, a.mode)
    else:
        u = json.load(open(os.path.join(HERE, "out", "update.json")))
        ops = [{"op": "set", "path": "updates/" + u["run_id"], "data": u}]
    print(json.dumps(ops, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
