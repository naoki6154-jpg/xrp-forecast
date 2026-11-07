"""手で登録する出来事（facts/events_manual.csv：事件・規制・ETF・政治など）を facts/events.csv に合流させる。

events_manual.csv は人（📰 イベント台帳チャットなど）が編集する台帳。1行1件で、時刻が確認できないものは
datetime_utc を空欄にする（値動きは日足で計算される）。出典URLと確認日（checked_at）は必須。
"""
import csv
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "facts"))
sys.path.insert(0, os.path.join(ROOT, "fetchers"))
from build_macro import EVENT_FIELDS  # noqa: E402
from common import merge_csv  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


def main():
    with open(os.path.join(ROOT, "facts", "events_manual.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        jst = ""
        if r["datetime_utc"]:
            t = datetime.strptime(r["datetime_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
            jst = t.astimezone(JST).strftime("%Y-%m-%d %H:%M")
        out.append(dict(event_id=r["event_id"], category=r["category"], subcategory=r["subcategory"], name=r["name"],
                        date_et=r["date_et"], datetime_utc=r["datetime_utc"], datetime_jst=jst,
                        time_basis=r["time_basis"], reference_period="", details=r["details"],
                        source=f"{r['source']}（{r['source_type']}、{r['checked_at']}確認）",
                        source_url=r["source_url"], recorded_at=r["checked_at"]))
    merge_csv(os.path.join(ROOT, "facts", "events.csv"), out, ["event_id"], EVENT_FIELDS)
    print(f"手で登録した出来事：{len(out)}件を合流")


if __name__ == "__main__":
    main()
