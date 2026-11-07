"""Google Cloud の費用を、読むだけの権限で毎日取得する（設計書 16-2・16-3）。

仕組み：
- Google Cloud の「請求データの BigQuery へのエクスポート（標準の使用料金）」を、
  プロジェクト xrp-forecast のデータセット billing_export に書き出してもらう（利用者が一度だけ設定）。
- GitHub Actions（gcp-cost.yml）が、鍵を使わない連携（Workload Identity）で
  読むだけの係（サービスアカウント gcp-cost-reader）として BigQuery を読み、
  日別・サービス別の金額を data/raw/ops/gcp_cost.csv に保存する。
- 状況（最後に取れた日・取れなかった理由）は data/raw/ops/gcp_cost_status.json に書く。

state/latest.json と _fetch_log.csv は書かない（毎時の取得と保存がぶつからないように）。
エクスポートの設定前・データ待ちのときは「未取得」と記録して正常に終わる（空欄を推測で埋めない）。
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RAW, merge_csv, now_utc_iso  # noqa: E402

PROJECT = "xrp-forecast"
DATASET = "billing_export"
OUT = os.path.join(RAW, "ops", "gcp_cost.csv")
STATUS = os.path.join(RAW, "ops", "gcp_cost_status.json")
FIELDS = ["date_jst", "service", "currency", "cost", "credits_promotion", "credits_other", "net",
          "source", "fetched_at"]

SQL = f"""
SELECT
  FORMAT_DATE('%Y-%m-%d', DATE(usage_start_time, 'Asia/Tokyo')) AS date_jst,
  service.description AS service,
  currency,
  ROUND(SUM(cost), 6) AS cost,
  ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c WHERE c.type = 'PROMOTION'), 0)), 6)
    AS credits_promotion,
  ROUND(SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c WHERE c.type != 'PROMOTION'), 0)), 6)
    AS credits_other
FROM `{PROJECT}.{DATASET}.gcp_billing_export_v1_*`
WHERE DATE(usage_start_time, 'Asia/Tokyo') >= DATE_SUB(CURRENT_DATE('Asia/Tokyo'), INTERVAL 40 DAY)
GROUP BY 1, 2, 3
ORDER BY 1, 2
"""


def write_status(ok, note, last=""):
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(dict(ok=ok, checked_at=now_utc_iso(), last_date_jst=last, note=note), f,
                  ensure_ascii=False, indent=2)
    print(("[OK]   " if ok else "[未取得] ") + note)


def main():
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and not os.environ.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"):
        write_status(False, "Google Cloud との連携が未設定（cloud/gcp_cost/setup.sh をまだ実行していない可能性）")
        return 0
    cmd = ["bq", "query", "--quiet", "--use_legacy_sql=false", "--format=json", f"--project_id={PROJECT}",
           "--max_rows=10000", SQL]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        err = (p.stderr or p.stdout).strip().replace("\n", " ")[:300]
        if "Not found" in err or "does not match any table" in err:
            write_status(False, "請求データのエクスポートがまだ届いていない（設定直後は数時間〜1日かかる）：" + err[:120])
            return 0
        write_status(False, "BigQuery の読み取りに失敗：" + err)
        return 1
    rows = json.loads(p.stdout or "[]")
    fetched_at = now_utc_iso()
    out = []
    for r in rows:
        cost = float(r["cost"] or 0)
        cp = float(r["credits_promotion"] or 0)
        co = float(r["credits_other"] or 0)
        out.append(dict(date_jst=r["date_jst"], service=r["service"], currency=r["currency"],
                        cost=r["cost"], credits_promotion=r["credits_promotion"], credits_other=r["credits_other"],
                        net=f"{cost + cp + co:.6f}",
                        source="Google Cloud 請求データの BigQuery エクスポート（標準の使用料金）"
                               f" {PROJECT}.{DATASET}",
                        fetched_at=fetched_at))
    if not out:
        write_status(False, "エクスポートの表はあるが、直近40日の行がない（データ待ち）")
        return 0
    total, added = merge_csv(OUT, out, ["date_jst", "service"], FIELDS)
    last = max(r["date_jst"] for r in out)
    month = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m")
    mtd = sum(float(r["net"]) for r in out if r["date_jst"].startswith(month))
    write_status(True, f"{len(out)}行（全体 {total}行）。今月の合計（クレジット差し引き後）{mtd:.2f} {out[0]['currency']}", last)
    return 0


if __name__ == "__main__":
    sys.exit(main())
