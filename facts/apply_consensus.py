"""市場予想（facts/consensus.csv）を facts/event_values.csv に反映し、サプライズ（結果－予想）を計算する。

- 市場予想は二次情報（経済指標カレンダーのサイト）。出典URLと取得日は consensus.csv に残す。
- サプライズの「結果」は、一次データ（FRED/ALFRED）から計算した値を、公表の桁（CPIは小数1桁）に丸めて使う。
- サイトに載っている結果（source_actual）と一次データの値が食い違う行は、facts/consensus_check.csv に書き出す。
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTS = os.path.join(ROOT, "facts")
sys.path.insert(0, FACTS)
from build_macro import VALUE_FIELDS  # noqa: E402

ROUND = {"CPI 前年比": 1, "コアCPI 前年比": 1, "CPI 前月比": 1, "コアCPI 前月比": 1,
         "非農業部門雇用者数 前月差": 0, "誘導目標 上限": 2}


def main():
    with open(os.path.join(FACTS, "consensus.csv"), newline="", encoding="utf-8") as f:
        cons = {(r["event_id"], r["metric"]): r for r in csv.DictReader(f)}
    path = os.path.join(FACTS, "event_values.csv")
    with open(path, newline="", encoding="utf-8") as f:
        vals = list(csv.DictReader(f))
    have = {(v["event_id"], v["metric"]) for v in vals}
    checks, applied = [], 0
    for v in vals:
        c = cons.get((v["event_id"], v["metric"]))
        if not c or not v["value"]:
            continue
        nd = ROUND.get(v["metric"], 2)
        actual = round(float(v["value"]), nd)
        v["consensus"] = c["consensus"]
        v["surprise"] = f"{actual - float(c['consensus']):.{max(nd, 1)}f}"
        v["source"] = v["source"].split("／予想：")[0] + f"／予想：{c['source_url']}（{c['retrieved_at']}取得、二次情報）"
        applied += 1
        diff = actual - float(c["source_actual"])
        checks.append(dict(event_id=v["event_id"], metric=v["metric"], primary_value=f"{actual}",
                           site_actual=c["source_actual"], diff=f"{diff:.2f}",
                           match="一致" if abs(diff) < 0.051 or (nd == 0 and abs(diff) < 0.5) else "食い違い"))
    missing = [k for k in cons if k not in have]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=VALUE_FIELDS)
        w.writeheader()
        w.writerows(vals)
    with open(os.path.join(FACTS, "consensus_check.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["event_id", "metric", "primary_value", "site_actual", "diff", "match"])
        w.writeheader()
        w.writerows(checks)
    ng = [c for c in checks if c["match"] != "一致"]
    print(f"市場予想を反映：{applied}件（出来事が未登録で反映できず：{len(missing)}件 {missing[:5]}）")
    print(f"一次データとサイトの結果の照合：一致 {len(checks) - len(ng)}件、食い違い {len(ng)}件")
    for c in ng:
        print("   食い違い", c)


if __name__ == "__main__":
    main()
