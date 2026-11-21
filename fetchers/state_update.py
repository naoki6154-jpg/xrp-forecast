"""別のワークフローで取得した項目の状況を state/latest.json の sources に反映する。

使い方：python3 fetchers/state_update.py etf_flows_xrp data/raw/flows/etf_xrp_status.json
毎時の取得（fetch-hourly）も state/latest.json を書くため、ワークフロー側で「最新を取り込む → この
プログラムで1項目だけ書き足す → 保存」を繰り返す（他の項目には触らない）。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(name, status_path):
    with open(os.path.join(ROOT, status_path), encoding="utf-8") as f:
        st = json.load(f)
    path = os.path.join(ROOT, "state", "latest.json")
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    entry = dict(ok=bool(st.get("ok")), fetched_at=st.get("checked_at", ""), rows=st.get("rows", ""),
                 added=st.get("added", ""), last=st.get("last", ""),
                 error=st.get("error") or st.get("last_error") or ("" if st.get("ok") else st.get("note", "")),
                 note=st.get("ticker_note", "") or st.get("note", ""))
    if st.get("last_failed_at"):
        entry["last_failed_at"] = st["last_failed_at"]
    state.setdefault("sources", {})[name] = entry
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"state/latest.json の sources.{name} を更新：ok={entry['ok']} last={entry['last']}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
