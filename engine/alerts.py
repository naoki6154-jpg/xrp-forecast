"""段階4：通知（アラート）の条件。設計書 第12章。
前回の定時更新（朝・夜。手動は除く）の記録と今回を比べ、条件に当たったものを並べる。
定時実行の報告の1行目に載り、携帯に通知される。ダッシュボードの updates/<run_id>.alerts にも入る。
"""
import glob, json, os
from datetime import datetime, timedelta


def previous_record(root, now_iso, run_id):
    best = None
    for fn in sorted(glob.glob(os.path.join(root, "forecasts", "*.json"))):
        b = os.path.basename(fn)
        if b == "latest.json" or "_manual" in b:
            continue
        try:
            r = json.load(open(fn))
        except Exception:
            continue
        if r.get("run_id") == run_id or r.get("at", "") >= now_iso:
            continue
        best = r
    return best


def check(root, rec, upd, cfg, fst):
    A = []
    acfg = cfg.get("alerts", {})
    prev = previous_record(root, rec["at"], rec["run_id"])
    s_now_u, s_now_j = rec["spot"]["usd"], rec["spot"]["jpy"]

    def add(level, kind, text):
        A.append({"level": level, "kind": kind, "text": text})

    if prev:
        pu, pj = prev["spot"]["usd"], prev["spot"]["jpy"]
        for L in acfg.get("levels_usd", [1.00, 1.10, 1.20, 1.25, 1.30, 1.50]):
            if (pu < L <= s_now_u):
                add("注意", "節目", "ドル価格が節目 $%.2f を上に抜けた（前回 $%.4f → 今 $%.4f）" % (L, pu, s_now_u))
            if (pu >= L > s_now_u):
                add("注意", "節目", "ドル価格が節目 $%.2f を割った（前回 $%.4f → 今 $%.4f）" % (L, pu, s_now_u))
        for L in acfg.get("levels_jpy", [150, 160, 170, 180, 190, 200, 205.5, 220]):
            if (pj < L <= s_now_j):
                add("注意", "節目", "円価格が節目 %.1f円 を上に抜けた（前回 %.2f円 → 今 %.2f円）" % (L, pj, s_now_j))
            if (pj >= L > s_now_j):
                add("注意", "節目", "円価格が節目 %.1f円 を割った（前回 %.2f円 → 今 %.2f円）" % (L, pj, s_now_j))
        th = acfg.get("p_change_pt", 0.10)
        pf = {f["id"]: f for f in prev.get("forecasts", [])}
        for f in rec["forecasts"]:
            o = pf.get(f["id"])
            if o and f["id"] == "1w" and abs(f["p_up_jpy"] - o["p_up_jpy"]) >= th:
                add("重要", "予想の変化", "1週間後に上がる確率が %.0f%% → %.0f%% に動いた（%+.0fポイント）" % (o["p_up_jpy"] * 100, f["p_up_jpy"] * 100, (f["p_up_jpy"] - o["p_up_jpy"]) * 100))
        # 到達確率（指値の代わりの節目）の変化
        if prev.get("touch") and rec.get("touch"):
            po = {(r["side"], r["pct"]): r for r in prev["touch"]["rows"]}
            for r in rec["touch"]["rows"]:
                o = po.get((r["side"], r["pct"]))
                if o and len(o["p"]) == len(r["p"]):
                    d = r["p"][-1] - o["p"][-1]
                    if abs(d) >= th:
                        add("注意", "到達確率", "年末までに%s%.0f%%に届く確率が %.0f%% → %.0f%%" % ("+" if r["side"] == "上" else "−", abs(r["pct"]) * 100, o["p"][-1] * 100, r["p"][-1] * 100))
        if prev.get("regime", {}).get("state") and prev["regime"]["state"] != rec["regime"]["state"]:
            add("重要", "相場の状態", "相場の状態（ルール）が「%s」→「%s」に切り替わった" % (prev["regime"]["state"], rec["regime"]["state"]))
        ph, nh = (prev.get("regime_hmm") or {}).get("now"), (rec.get("regime_hmm") or {}).get("now")
        if ph and nh and ph != nh:
            add("注意", "相場の状態", "統計で推定した相場の状態が「%s」→「%s」に切り替わった" % (ph, nh))
        # 重要イベントの結果（台帳に前回以降に記録された結果）
        try:
            import csv
            for r in csv.DictReader(open(os.path.join(root, "ledger/results.csv"))):
                if r.get("released_at_jst", "") > prev["at"] and r.get("released_at_jst", "") <= rec["at"]:
                    add("重要", "イベントの結果", "%s：%s（発表 %s、予想 %s、区分 %s）" % (r["name"], r.get("metric"), r.get("actual"), r.get("consensus"), r.get("outcome_label")))
        except Exception:
            pass
    # 底入れのサイン：資金調達率のマイナス転換
    fund = [x["value"] for x in (upd.get("derivatives") or {}).get("funding", []) if x.get("value") is not None]
    if fund and sum(fund) / len(fund) < 0:
        add("注意", "底入れ・天井のサイン", "資金調達率の平均がマイナス（%.4f%%）。売りに傾いた状態（底入れのサインになることがある）" % (sum(fund) / len(fund) * 100))
    # データが24時間以上更新できていない
    now = datetime.fromisoformat(rec["at"])
    for f in (fst or {}).get("freshness", []):
        if f.get("age_min") is not None and f["age_min"] > 24 * 60:
            add("重要", "データの停止", "%s が %.0f時間更新されていない" % (f.get("source") or f.get("name"), f["age_min"] / 60))
    for f in (fst or {}).get("fetchers", []):
        try:
            age = (now - datetime.fromisoformat(f["fetched_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        except Exception:
            continue
        if age > 24:
            add("重要", "データの停止", "取得先 %s が %.0f時間取れていない" % (f.get("source"), age))
    return {"items": A, "previous_run": prev.get("run_id") if prev else None,
            "rule": "前回の定時更新との比較：節目の価格（ドル・円）を抜けた／割った、1週間後に上がる確率が10ポイント以上動いた、年末までの到達確率が10ポイント以上動いた、相場の状態の切り替わり、重要イベントの結果、資金調達率のマイナス、データの24時間以上の停止"}
