"""週1回の振り返り（設計書16-2）。③検証担当が毎週日曜 20:10 に実行する。毎日の定時更新（run.py）とは別の仕組み。

使い方：  python3 engine/weekly.py [--now 2026-11-15T20:10:00+09:00]
出力：
  reports/weekly/<YYYY>-W<ww>.md / .json   週ごとの振り返り（GitHubに保存）
  engine/out/weekly_ops.json               ダッシュボードの weekly/<YYYY>-W<ww> に書く1件分（db_write の ops）
  engine/out/weekly_summary.txt            通知の文面（問題がなければ5行以内）
リポジトリの data/・state/・forecasts/・docs/keys.md と GitHub Actions の実行記録を読むだけ。数字は全てこのプログラムが出す。
"""
import argparse, csv, glob, json, math, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JST = timezone(timedelta(hours=9))
REPO = "naoki6154-jpg/xrp-forecast"
SLOTS = [("morning", 7, 40), ("night", 21, 20)]
TRIAL_START = datetime(2026, 11, 8, 7, 40, tzinfo=JST)   # 定時更新の初回（朝）。これより前の枠は数えない
# まだ取れていない欄（段階1の時点）。取れるようになったら消す
NOT_YET = ["FedWatch（CME）", "経済指標の市場予想", "ニュース（②情報・イベント担当は段階2）", "Mac（補助）の取得", "運用中の予想の本格的な採点（③は段階3）"]
FREE_MIN_PRIVATE = 2000  # 非公開リポジトリの無料枠（分/月）。公開リポジトリは無料で上限なし


def P(*a):
    return os.path.join(ROOT, *a)


def ts(s):
    s = str(s).replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def read_csv(rel):
    p = P(rel)
    if not os.path.exists(p):
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


# ---------- 実際の価格（ドル） ----------
class Prices:
    def __init__(self):
        self.h = {}
        for r in read_csv("data/raw/prices/binance_xrp_usdt_1h.csv"):
            self.h[r["time"][:13]] = float(r["close"])   # 1時間足の始まりの時刻 → 終値
        self.spot = [(ts(r["time"]), float(r["lastPrice"])) for r in read_csv("data/raw/jp_relay/binance_spot_24h_xrp.csv")]
        self.src = "Binance XRP/USDT 1時間足の終値（なければ東京経由の現在値）"

    def at(self, t):
        u = t.astimezone(timezone.utc)
        k = (u - timedelta(hours=1)).strftime("%Y-%m-%dT%H")   # t の直前に終わった1時間足
        if k in self.h:
            return self.h[k]
        near = [p for (x, p) in self.spot if abs((x - u).total_seconds()) <= 90 * 60]
        return near[-1] if near else None


def load_records():
    out = []
    for fn in sorted(glob.glob(P("forecasts", "*.json"))):
        if os.path.basename(fn) == "latest.json":
            continue
        try:
            r = json.load(open(fn))
            r["_file"] = os.path.relpath(fn, ROOT)
            out.append(r)
        except Exception:
            pass
    return out


def score(records, prices, now, start):
    rows = []
    for r in records:
        s0 = r["spot"]["usd"]
        for f in r["forecasts"]:
            tg = datetime.fromisoformat(f["target"])
            if tg > now:
                continue
            px = prices.at(tg)
            if px is None:
                continue
            up = 1.0 if px > s0 else 0.0
            q = f["q_usd"]
            rows.append({"run": r["run_id"], "mode": r["mode"], "h": f["id"], "label": f["label"], "target": f["target"], "in_week": tg >= start,
                         "p": f["p_up_usd"], "up": up, "hit_dir": (f["p_up_usd"] >= 0.5) == (up == 1.0),
                         "in50": q[1] <= px <= q[3], "in80": q[0] <= px <= q[4], "brier": (f["p_up_usd"] - up) ** 2, "actual": px, "s0": s0})

    def agg(rs):
        n = len(rs)
        if not n:
            return {"n": 0}
        return {"n": n, "dir": sum(x["hit_dir"] for x in rs) / n, "in50": sum(x["in50"] for x in rs) / n, "in80": sum(x["in80"] for x in rs) / n,
                "brier": sum(x["brier"] for x in rs) / n, "brier_base": 0.25, "reference_only": n < 20}
    week = [x for x in rows if x["in_week"]]
    by_h = {h: agg([x for x in rows if x["h"] == h]) for h in ("1d", "1w", "1m", "ye")}
    return {"week": agg(week), "all": agg(rows), "by_horizon_all": by_h, "price_source": prices.src}


def slots(start, end):
    out = []
    d = start.date()
    while d <= end.date():
        for mode, h, m in SLOTS:
            t = datetime(d.year, d.month, d.day, h, m, tzinfo=JST)
            if max(start, TRIAL_START) <= t <= end - timedelta(minutes=30):
                out.append((mode, t))
        d += timedelta(days=1)
    return out


def operations(records, start, end):
    sl = slots(start, end)
    done, missed = 0, []
    for mode, t in sl:
        ok = any(r["mode"] == mode and t - timedelta(minutes=10) <= datetime.fromisoformat(r["at"]) <= t + timedelta(minutes=120) for r in records)
        if ok:
            done += 1
        else:
            missed.append(t.strftime("%m/%d ") + ("朝" if mode == "morning" else "夜"))
    return {"planned": len(sl), "done": done, "missed": missed, "basis": "forecasts/ に朝・夜の予想の記録が保存された回を成功として数える"}


def data_gaps(records, start, end):
    log = [r for r in read_csv("data/raw/_fetch_log.csv") if start <= ts(r["run_at"]) <= end]
    src = {}
    for r in log:
        s = src.setdefault(r["source"], [0, 0, []])
        s[0] += 1; s[1] += int(r["ok"] == "1")
        if r["ok"] != "1" and r.get("error"):
            s[2].append(r["error"][:80])
    rates = [{"source": k, "runs": v[0], "ok": v[1], "rate": v[1] / v[0], "errors": sorted(set(v[2]))[:3]} for k, v in sorted(src.items())]
    wk = [r for r in records if start <= datetime.fromisoformat(r["at"]) <= end]
    fb_jpy = sum(1 for r in wk if r["spot"]["jpy_source"].startswith("ドル価格"))
    fb_usd = sum(1 for r in wk if not r["spot"]["usd_source"].startswith("Binance 現物"))
    st = json.load(open(P("state/latest.json")))
    now_bad = [k for k, v in st.get("sources", {}).items() if not v.get("ok")]
    blanks = list(NOT_YET)
    if wk and not wk[-1].get("fed"):
        blanks.insert(0, "12月FOMCの予測市場の確率")
    return {"fetch": rates, "fetch_runs": len(log), "fallback": {"XRP円価格": fb_jpy, "XRPドル価格": fb_usd, "records": len(wk)},
            "failing_now": now_bad, "blank_items": blanks}


def actions_minutes(start, end):
    try:
        out = subprocess.run(["gh", "api", "--paginate", "repos/%s/actions/runs?per_page=100&created=>=%s" % (REPO, start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                              "--jq", ".workflow_runs[] | [.name, .conclusion, .run_started_at, .updated_at] | @tsv"],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            return {"ok": False, "error": out.stderr.strip()[:200]}
        tot, n, fail, canc, by = 0.0, 0, 0, 0, {}
        for line in out.stdout.strip().splitlines():
            name, concl, a, b = (line.split("\t") + ["", "", "", ""])[:4]
            if not a or not b:
                continue
            m = max(0.0, (ts(b) - ts(a)).total_seconds() / 60)
            if ts(a) > end:
                continue
            tot += m; n += 1
            if concl == "cancelled":
                canc += 1   # 取り消し（順番待ちの自動取り消し・手動停止）は失敗と分けて数える
            elif concl not in ("success", "skipped", "neutral", ""):
                fail += 1
            by[name] = by.get(name, 0) + m
        pub = subprocess.run(["gh", "api", "repos/" + REPO, "--jq", ".private"], capture_output=True, text=True, timeout=60).stdout.strip()
        return {"ok": True, "minutes": round(tot, 1), "runs": n, "failed_runs": fail, "cancelled_runs": canc, "by_workflow": {k: round(v, 1) for k, v in by.items()},
                "private": pub == "true", "ratio_free_private_monthly": round(tot * 4.3 / FREE_MIN_PRIVATE, 3),
                "note": "公開リポジトリは無料で上限なし。参考として非公開の無料枠（月2,000分）に対する1ヶ月換算の割合も出す。分数は実行の開始〜終了の時間の合計（課金の分数とは丸め方が違う）"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def gcp_cost(start, end):
    """Google Cloud の費用（🗄️データ基盤の gcp-cost が保存する data/raw/ops/gcp_cost.csv）。届いていなければ理由を返す。"""
    st = {}
    try:
        st = json.load(open(P("data/raw/ops/gcp_cost_status.json")))
    except Exception:
        pass
    rows = read_csv("data/raw/ops/gcp_cost.csv")
    if not rows:
        return {"ok": False, "week": None, "month": None, "note": "未取得：" + (st.get("note") or "費用のファイルがまだない")[:120]}
    d0, d1 = start.date().isoformat(), end.date().isoformat()
    month = end.strftime("%Y-%m")
    wk = sum(float(r["net"]) for r in rows if d0 <= r["date_jst"] <= d1)
    mtd = sum(float(r["net"]) for r in rows if r["date_jst"].startswith(month))
    cur = rows[-1].get("currency", "")
    return {"ok": True, "week": wk, "month": mtd, "currency": cur, "last_date": max(r["date_jst"] for r in rows),
            "note": "クレジット差し引き後の金額。最新の日付 %s（請求データは1日ほど遅れて届く）" % max(r["date_jst"] for r in rows)}


def keys_due(now):
    p = P("docs/keys.md")
    if not os.path.exists(p):
        return {"ok": False, "items": [], "note": "docs/keys.md がありません"}
    items = []
    for line in open(p):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not re.match(r"\d{4}-\d{2}-\d{2}", cells[3]):
            continue
        due = datetime.fromisoformat(cells[3][:10] + "T00:00:00+09:00")
        days = (due.date() - now.date()).days
        items.append({"name": cells[0], "place": cells[1], "due": cells[3][:10], "days": days, "soon": days <= 30})
    return {"ok": True, "items": items, "soon": [i for i in items if i["soon"]]}


def changes(records, start, end):
    wk = [r for r in records if datetime.fromisoformat(r["at"]) <= end]
    if not wk:
        return None
    last = wk[-1]
    base = [r for r in wk if datetime.fromisoformat(r["at"]) <= end - timedelta(days=7)]
    first = base[-1] if base else wk[0]
    if first is last:
        return {"from": last["at"], "to": last["at"], "items": [], "reasons": ["比べる前の予想がまだありません（最初の週）"]}
    items = []
    fp = {f["id"]: f for f in first["forecasts"]}
    for f in last["forecasts"]:
        g = fp.get(f["id"])
        if g:
            items.append({"h": f["id"], "label": f["label"], "from": g["p_up_jpy"], "to": f["p_up_jpy"], "diff": f["p_up_jpy"] - g["p_up_jpy"]})
    rs = []
    dp = last["spot"]["jpy"] / first["spot"]["jpy"] - 1
    rs.append("価格：%.1f円 → %.1f円（%+.1f%%）" % (first["spot"]["jpy"], last["spot"]["jpy"], dp * 100))
    dv = last["vol"]["annual"] - first["vol"]["annual"]
    rs.append("変動率（年率）：%.0f%% → %.0f%%" % (first["vol"]["annual"] * 100, last["vol"]["annual"] * 100) + ("（範囲が広がる向き）" if dv > 0.03 else "（範囲が狭まる向き）" if dv < -0.03 else ""))
    if first.get("fed") and last.get("fed"):
        rs.append("12月FOMCの利上げ確率（予測市場）：%.0f%% → %.0f%%" % (first["fed"]["p_hike"] * 100, last["fed"]["p_hike"] * 100))
    w0 = first.get("weights", {}); w1 = last.get("weights", {})
    if w0 and w1 and abs(w0.get("A", 0) - w1.get("A", 0)) > 0.001:
        rs.append("モデルの重み：A %.0f%%・C %.0f%% → A %.0f%%・C %.0f%%" % (w0["A"] * 100, w0["C"] * 100, w1["A"] * 100, w1["C"] * 100))
    gone = {e["id"] for e in first.get("events", [])} - {e["id"] for e in last.get("events", [])}
    names = {e["id"]: e["name"] for e in first.get("events", [])}
    if gone:
        rs.append("発表済み・期間外になったイベント：" + "、".join(names[g] for g in sorted(gone)) + "（結果は📰イベント台帳で確定させる）")
    return {"from": first["at"], "to": last["at"], "items": items, "reasons": rs}


def next_week(records, now):
    out = []
    if records:
        for e in records[-1].get("events", []):
            w = e.get("when", "")
            if "T" in w:
                t = datetime.fromisoformat(w)
                if now <= t <= now + timedelta(days=7):
                    out.append({"when": w, "name": e["name"], "impact": e.get("impact_size"), "swing": e.get("swing_p_up_ye")})
            elif "〜" in w:
                a, b = w.split("〜")
                if a <= (now + timedelta(days=7)).date().isoformat() and b >= now.date().isoformat():
                    out.append({"when": w, "name": e["name"], "impact": e.get("impact_size"), "swing": e.get("swing_p_up_ye")})
    names = {"10": "米CPI", "50": "米雇用統計", "54": "米PCE"}
    have = {x["name"][:4] for x in out}
    for fid, nm in names.items():
        for r in read_csv("data/raw/fred/release_dates_%s.csv" % fid):
            d = r["release_date"]
            if now.date().isoformat() <= d <= (now + timedelta(days=7)).date().isoformat() and nm[:4] not in have:
                out.append({"when": d, "name": nm + "（FREDの発表日。イベント表に未登録）", "impact": None, "swing": None})
    return sorted(out, key=lambda x: x["when"])


def improvements(sc, ops, gaps, recs, act):
    c = []
    if recs:
        bt = recs[-1].get("backtest_c", {})
        for k, v in bt.items():
            if v["in50"] > 0.6 or v["in50"] < 0.4:
                c.append("モデルCの%s日の50%%範囲の的中率が%.0f%%（目安50%%）。範囲が%s可能性" % (k, v["in50"] * 100, "広すぎる" if v["in50"] > 0.6 else "狭すぎる"))
        last = recs[-1]
        sp = [f for f in last["forecasts"] if f.get("model_spread", 0) > 0.10]
        if sp:
            c.append("モデルAとCの差が10ポイントを超える期間あり（" + "、".join(f["label"] for f in sp) + "）。判断で置いたイベント確率の見直し候補")
        nj = sum(1 for e in last.get("events", []) if e.get("p_basis") == "判断")
        if nj:
            c.append("判断で置いたイベント確率が%d件。予測市場・過去事実データベースで置き換えられるものを探す" % nj)
    for s in gaps["fetch"]:
        if s["rate"] < 0.9:
            c.append("%s の取得の成功率が%.0f%%" % (s["source"], s["rate"] * 100))
    if gaps["fallback"]["XRP円価格"]:
        c.append("円価格の代わりの取得先を%d回使用（bitbankの毎時取得が入った後は減るはず）" % gaps["fallback"]["XRP円価格"])
    w = sc["week"]
    if w.get("n", 0) >= 20 and w["brier"] > 0.25:
        c.append("今週の上がる確率のブライアスコア %.3f が基準（0.25）より悪い" % w["brier"])
    return [x + "（実施は🔬検証・改良チャットで確かめてから）" for x in c]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", default=None)
    a = ap.parse_args()
    now = (datetime.fromisoformat(a.now) if a.now else datetime.now(JST)).astimezone(JST).replace(second=0, microsecond=0)
    start = now - timedelta(days=7)
    iso = now.isocalendar()
    wid = "%d-W%02d" % (iso[0], iso[1])
    recs = load_records()
    sc = score(recs, Prices(), now, start)
    ops = operations(recs, start, now)
    gaps = data_gaps(recs, start, now)
    act = actions_minutes(start, now)
    gcp = gcp_cost(start, now)
    keys = keys_due(now)
    ch = changes(recs, start, now)
    nw = next_week(recs, now)
    imp = improvements(sc, ops, gaps, recs, act)

    # ---------- 問題の判定（決まった規則だけ） ----------
    issues = []  # (内容, 頼むチャット, 頼み方)
    if ops["missed"]:
        issues.append(("定時更新が%d回動かなかった（%s）" % (len(ops["missed"]), "、".join(ops["missed"])), "🧭 司令塔", "「定時更新が動かなかった回があります。定時実行の状態を確認して」"))
    for s in gaps["fetch"]:
        if s["rate"] < 0.9:
            issues.append(("%sの取得の成功率が%.0f%%" % (s["source"], s["rate"] * 100), "🗄️ データ基盤", "「%sの取得が失敗しています。直して」" % s["source"]))
    if gaps["failing_now"]:
        issues.append(("今も失敗中の取得先：" + "、".join(gaps["failing_now"]), "🗄️ データ基盤", "「%sの取得が失敗しています。直して」" % "、".join(gaps["failing_now"])))
    if act.get("ok") and act["failed_runs"]:
        issues.append(("GitHub Actionsの失敗が%d回" % act["failed_runs"], "🗄️ データ基盤", "「GitHub Actionsの失敗が続いています。原因を調べて直して」"))
    for k in keys.get("soon", []):
        issues.append(("鍵「%s」の取り替え期限まで%d日（%s）" % (k["name"], k["days"], k["due"]), "🗄️ データ基盤", "「%sを取り替えて、docs/keys.md の日付を更新して」" % k["name"]))
    if not act.get("ok"):
        issues.append(("GitHub Actionsの使用分数を取得できず", "🧭 司令塔", "「週次の振り返りで Actions の分数が取れません。確認して」"))

    rep = {"week": wid, "at": now.isoformat(), "period": [start.isoformat(), now.isoformat()], "score": sc, "operations": ops, "data": gaps,
           "cost": {"gcp": gcp,
                    "actions": act, "claude": {"note": "定時実行は1日2回＋週1回。利用の上限に当たった回は「稼働」の止まった回に出る"}},
           "changes": ch, "next_week": nw, "improvements": imp, "keys": keys,
           "issues": [{"what": i[0], "chat": i[1], "ask": i[2]} for i in issues]}
    os.makedirs(P("reports", "weekly"), exist_ok=True)
    json.dump(rep, open(P("reports", "weekly", wid + ".json"), "w"), ensure_ascii=False, indent=1)

    # ---------- Markdown ----------
    def pc(x): return "—" if x is None else "%.0f%%" % (x * 100)
    w, al = sc["week"], sc["all"]
    M = ["# 週次の振り返り %s" % wid, "", "期間：%s 〜 %s（日本時間）。作成：③検証担当（定時実行）。数字は engine/weekly.py の計算。投資助言ではない。" % (start.strftime("%m/%d %H:%M"), now.strftime("%m/%d %H:%M")), ""]
    M += ["## 問題と頼み先", ""] + (["- %s → %sに%s" % i for i in issues] if issues else ["- なし"]) + [""]
    M += ["## 予想の採点", "", "| 対象 | 件数 | 方向の当たり率 | 50%範囲に入った | 80%範囲に入った | ブライアスコア（基準0.25） |", "| --- | --- | --- | --- | --- | --- |"]
    for nm, x in (("今週期限", w), ("これまで全部", al)):
        M.append("| %s | %d | %s | %s | %s | %s |" % (nm, x.get("n", 0), pc(x.get("dir")), pc(x.get("in50")), pc(x.get("in80")), "—" if not x.get("n") else "%.3f" % x["brier"]))
    M += ["", "件数が20件未満は参考値。実際の価格：%s。" % sc["price_source"], ""]
    M += ["## 稼働", "", "- 定時更新：%d／%d回成功（%s）" % (ops["done"], ops["planned"], ops["basis"])] + (["- 動かなかった回：" + "、".join(ops["missed"])] if ops["missed"] else []) + [""]
    M += ["## データの欠け", "", "| 取得先 | 実行回数 | 成功 | 成功率 |", "| --- | --- | --- | --- |"] + ["| %s | %d | %d | %s |" % (s["source"], s["runs"], s["ok"], pc(s["rate"])) for s in gaps["fetch"]]
    M += ["", "- 代わりの取得先を使った回数（予想%d回中）：円価格 %d回・ドル価格 %d回" % (gaps["fallback"]["records"], gaps["fallback"]["XRP円価格"], gaps["fallback"]["XRPドル価格"]),
          "- 空欄の項目：" + "、".join(gaps["blank_items"]), ""]
    M += ["## 費用", "", ("- Google Cloud：今週 %.2f %s・今月 %.2f %s（%s）" % (gcp["week"], gcp["currency"], gcp["month"], gcp["currency"], gcp["note"])) if gcp["ok"] else "- Google Cloud：" + gcp["note"] + "（それまでは予算アラートのメールで確認）"]
    if act.get("ok"):
        M.append("- GitHub Actions：%.0f分（%d回、失敗%d回・取り消し%d回）。%s。非公開だった場合の無料枠に対する1ヶ月換算：%s" % (act["minutes"], act["runs"], act["failed_runs"], act.get("cancelled_runs", 0), "公開リポジトリのため無料・上限なし" if not act["private"] else "非公開リポジトリ", pc(act["ratio_free_private_monthly"])))
    else:
        M.append("- GitHub Actions：取得できず（%s）" % act.get("error"))
    M += ["", "## 予想の変化", ""]
    if ch:
        M += ["%s → %s" % (ch["from"], ch["to"]), ""] + ["- %s：上がる確率 %s → %s（%+.0fポイント）" % (i["label"], pc(i["from"]), pc(i["to"]), i["diff"] * 100) for i in ch["items"]] + ["- " + r for r in ch["reasons"]]
    M += ["", "## 来週の予定", ""] + (["- %s　%s（影響 %s）" % (x["when"][:16].replace("T", " ") if "T" in x["when"] else x["when"], x["name"], x["impact"] or "—") for x in nw] or ["- 登録なし"])
    M += ["", "## 改善の候補", ""] + (["- " + x for x in imp] or ["- なし"])
    M += ["", "## 鍵の期限（30日以内）", ""] + (["- %s：%s（あと%d日）" % (k["name"], k["due"], k["days"]) for k in keys.get("soon", [])] or ["- なし"]) + [""]
    open(P("reports", "weekly", wid + ".md"), "w").write("\n".join(M))

    # ---------- ダッシュボードの1件分 ----------
    def r5(o):
        if isinstance(o, float): return float("%.4g" % o)
        if isinstance(o, dict): return {k: r5(v) for k, v in o.items()}
        if isinstance(o, list): return [r5(v) for v in o]
        return o
    doc = r5({"week": wid, "at": now.isoformat(), "period": rep["period"], "issues": rep["issues"],
              "score": {"week": w, "all": al}, "operations": {k: ops[k] for k in ("planned", "done", "missed")},
              "data": {"fetch": [{k: s[k] for k in ("source", "runs", "ok", "rate")} for s in gaps["fetch"]], "fallback": gaps["fallback"], "blank_items": gaps["blank_items"]},
              "cost": {"gcp": gcp.get("week") if gcp["ok"] else None, "gcp_month": gcp.get("month"), "gcp_currency": gcp.get("currency"), "gcp_note": gcp["note"], "actions_minutes": act.get("minutes"), "actions_runs": act.get("runs"), "actions_failed": act.get("failed_runs"), "actions_cancelled": act.get("cancelled_runs"), "private": act.get("private")},
              "changes": ch, "next_week": nw, "improvements": imp, "keys": keys.get("items", []), "file": "reports/weekly/%s.md" % wid})
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    json.dump([{"op": "set", "path": "weekly/" + wid, "data": doc}], open(os.path.join(HERE, "out", "weekly_ops.json"), "w"), ensure_ascii=False, separators=(",", ":"))

    # ---------- 通知 ----------
    if issues:
        L = ["【要対応 %d件】%sに%s" % (len(issues), issues[0][1], issues[0][2])] + ["・" + i[0] + "（" + i[1] + "）" for i in issues[:4]]
    else:
        L = ["週次の振り返り %s：問題なし" % wid]
    L.append("定時更新 %d/%d回・採点 %d件（方向%s・80%%範囲%s）" % (ops["done"], ops["planned"], w.get("n", 0), pc(w.get("dir")), pc(w.get("in80"))))
    if ch and ch["items"]:
        x = [i for i in ch["items"] if i["h"] == "1w"]
        if x: L.append("1週間後に上がる確率 %s→%s" % (pc(x[0]["from"]), pc(x[0]["to"])))
    L.append("来週：" + ("、".join(y["name"].split("（")[0] for y in nw[:3]) or "登録なし"))
    L.append("詳細：ダッシュボードの「週次の振り返り」と reports/weekly/%s.md" % wid)
    if not issues:
        L = L[:5]
    open(os.path.join(HERE, "out", "weekly_summary.txt"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
