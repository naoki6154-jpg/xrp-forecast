"""📰イベント台帳：calendar.json・news.json・results.csv から、ダッシュボード用のデータと一覧表を作る。

使い方（リポジトリの一番上で）：  python3 ledger/build.py
出力：
  ledger/out/dashboard_ledger.json … ダッシュボードの ledger/events・ledger/news に書く中身
  ledger/calendar.csv               … 予定表を1イベント1行にしたもの（人が見る用）
  ledger/out/results_view.json      … 結果と、その後のXRPの値動き（1時間・4時間・1日）
予想エンジン（engine/）・facts/ のファイルは読むだけで、書き換えない。
"""
import csv, json, os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
L = os.path.join(ROOT, "ledger")
JST = timezone(timedelta(hours=9))


def load_hourly(name):
    """Binance 1時間足（data/raw/prices/）。{開始時刻(UTC): (始値, 終値)}"""
    p = os.path.join(ROOT, "data", "raw", "prices", name)
    out = {}
    if not os.path.exists(p):
        return out
    for r in csv.DictReader(open(p, encoding="utf-8")):
        out[r["time"]] = (float(r["open"]), float(r["close"]))
    return out


def reaction(series, t_utc, hours):
    """facts/reactions.csv と同じ基準：発表直前の正時の価格 → h時間後の最初の正時の価格。"""
    base_hour = t_utc.replace(minute=0, second=0, microsecond=0)
    k0 = base_hour.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_hour = base_hour + timedelta(hours=hours) if t_utc.minute == 0 else base_hour + timedelta(hours=hours + 1)
    k1 = end_hour.strftime("%Y-%m-%dT%H:%M:%SZ")  # その正時に始まる足の始値
    if k0 not in series or k1 not in series:
        return None
    p0 = series[k0][0]  # 直前の正時＝その足の始値
    p1 = series[k1][0]
    return round((p1 / p0 - 1) * 100, 3)


def main():
    cal = json.load(open(os.path.join(L, "calendar.json"), encoding="utf-8"))
    news = json.load(open(os.path.join(L, "news.json"), encoding="utf-8"))
    results = list(csv.DictReader(open(os.path.join(L, "results.csv"), encoding="utf-8")))
    xrp, btc = load_hourly("binance_xrp_usdt_1h.csv"), load_hourly("binance_btc_usdt_1h.csv")

    # 結果とその後の値動き
    rv = []
    for r in results:
        t = datetime.fromisoformat(r["released_at_jst"]).astimezone(timezone.utc)
        row = dict(r)
        for h, lab in ((1, "1時間"), (4, "4時間"), (24, "1日")):
            x, b = reaction(xrp, t, h), reaction(btc, t, h)
            row["xrp_" + lab] = x
            row["btc_" + lab] = b
            row["xrp_minus_btc_" + lab] = None if x is None or b is None else round(x - b, 3)
        row["reaction_basis"] = "Binance 1時間足（data/raw/prices/）。発表直前の正時の始値 → h時間後の最初の正時の始値（facts/reactions.csv と同じ）。空欄はデータ待ち（前日分まで）"
        rv.append(row)
    json.dump(rv, open(os.path.join(L, "out", "results_view.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 予定表の一覧（CSV）
    with open(os.path.join(L, "calendar.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "kind", "impact", "when_jst", "time_basis", "consensus", "outcomes", "p_basis", "p_source", "p_reason", "source", "source_url", "checked_at", "engine_id"])
        for e in cal["events"]:
            w.writerow([e["id"], e["name"], e["kind"], e["impact"], e["when"], e["time_basis"],
                        " / ".join(c["metric"] + " " + c["value"] for c in e.get("consensus", []) if c.get("value")),
                        " / ".join("%s %d%%" % (o["label"], round(o["p"] * 100)) for o in e["outcomes"]),
                        e["p_basis"], e.get("p_source", ""), e.get("p_reason", ""), e["source"], e["source_url"], e["checked_at"], e.get("engine_id", "")])

    # 確率の合計を点検
    for e in cal["events"]:
        s = sum(o["p"] for o in e["outcomes"])
        assert abs(s - 1) < 0.005, (e["id"], s)

    # ダッシュボード用
    res_by_cal = {r["calendar_id"]: r for r in rv}
    dash_events = {
        "at": datetime.now(JST).isoformat(timespec="seconds"),
        "by": "📰 イベント台帳チャット",
        "reference_only": True,
        "note": "参考表示：試運転（11/8〜11/14）が終わるまで予想の計算には使っていません。＊は判断で置いた確率（理由は ledger/calendar.json）。",
        "market_snapshot": cal.get("market_snapshot", ""),
        "items": [{k: e.get(k) for k in ("id", "name", "kind", "impact", "when", "time_basis", "consensus", "note", "outcome_rule", "outcomes", "p_basis", "p_source", "p_reason", "source", "source_url", "checked_at", "engine_id")} for e in cal["events"]],
        "results": [{"name": r["name"], "at": r["released_at_jst"], "metric": r["metric"], "actual": r["actual"], "consensus": r["consensus"], "surprise": r["surprise"], "unit": r["unit"],
                     "outcome": r["outcome_label"], "xrp_1h": r["xrp_1時間"], "xrp_4h": r["xrp_4時間"], "xrp_1d": r["xrp_1日"], "btc_1d": r["btc_1日"], "source": r["actual_source"]} for r in rv],
    }
    dash_news = {"at": dash_events["at"], "by": "📰 イベント台帳チャット", "reference_only": True, "items": news["items"]}
    json.dump({"ledger/events": dash_events, "ledger/news": dash_news}, open(os.path.join(L, "out", "dashboard_ledger.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("予定 %d件・ニュース %d件・結果 %d件" % (len(cal["events"]), len(news["items"]), len(rv)))
    for r in rv:
        print(r["name"], "XRP 1時間", r["xrp_1時間"], "4時間", r["xrp_4時間"], "1日", r["xrp_1日"])


if __name__ == "__main__":
    main()
