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
SLOTS = [("night", 0, 10), ("morning", 6, 10), ("night", 12, 10), ("night", 18, 10)]  # 2026-11-17 修正：実際の定時（00:10・06:10・12:10・18:10）に合わせた。旧値07:40・21:20のままで「動かなかった」と誤判定していた
TRIAL_START = datetime(2026, 11, 9, 7, 40, tzinfo=JST)   # 試運転の数え始め（予想エンジン1.1に切り替えた次の朝。11/8の2回は1.0版）。これより前の枠は数えない
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


def _jpy_at(records, t, tol_min=45):
    """目標時刻に最も近い予想記録の円価格（bitbank・当日途中値）。45分以内。1時間足の円価格がリポジトリにないための代用"""
    best = None
    for r in records:
        d = abs((datetime.fromisoformat(r["at"]) - t).total_seconds()) / 60
        if d <= tol_min and (best is None or d < best[0]):
            best = (d, r["spot"]["jpy"])
    return best[1] if best else None


def score_extra(records, prices, now):
    """2026-11-25 🔬検証・改良の提案（利用者の了承済み）。score() の数字（補正前・ドル）は変えず、追加で出す。定時（朝・夜）の回だけ。
    ・画面に出る確率（p_up_jpy＝補正後＋モデルP）を、円の結果で採点する
    ・直前の傾向を使う基準（過去30日の上昇日の割合／過去60日の7日騰落が上昇だった割合）。「常に50%」より厳しい基準
    ・実質の件数（1日後＝目標日の数、1週間後＝予想を作った期間÷7）と、ブライア−0.25 の95%区間（同じ日・同じ週をまとめて補正）
    ・下落レジームで80%範囲が下に外れた割合（範囲の監視）
    失敗しても週次の振り返り本体は止めない（error を返す）。"""
    try:
        import numpy as np
        sched = [r for r in records if r["mode"] in ("morning", "night")]
        d1 = read_csv("data/raw/prices/binance_xrp_usdt_1d.csv")
        dates = [r["date"][:10] for r in d1]
        lc = np.log([float(r["close"]) for r in d1])

        def trail(at, h):
            d = (at.astimezone(timezone.utc) - timedelta(days=1)).date().isoformat()
            k = max(i for i, x in enumerate(dates) if x <= d)
            if h == "1d":
                w = lc[k - 29:k + 1] - lc[k - 30:k]
                return float((w > 0).mean())
            w = np.array([lc[j] - lc[j - 7] for j in range(k - 59, k + 1)])
            return float((w > 0).mean())

        def boot(a, blk, B=2000):
            a = np.asarray(a, float); n = len(a)
            if n < 2 * blk:
                return None
            rng = np.random.default_rng(20261125); out = []
            for _ in range(B):
                st = rng.integers(0, n, size=int(np.ceil(n / blk)))
                out.append(a[np.concatenate([(np.arange(x, x + blk)) % n for x in st])[:n]].mean())
            return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]

        res = {}
        for h, blk in (("1d", 4), ("1w", 28)):
            rows = []
            for r in sched:
                at = datetime.fromisoformat(r["at"])
                for f in r["forecasts"]:
                    if f["id"] != h:
                        continue
                    tg = datetime.fromisoformat(f["target"])
                    if tg > now:
                        continue
                    px = prices.at(tg)
                    if px is None:
                        continue
                    s0 = r["spot"]["usd"]; up = 1.0 if px > s0 else 0.0
                    jT = _jpy_at(records, tg); j0 = r["spot"]["jpy"]
                    upj = None if jT is None else (1.0 if jT > j0 else 0.0)
                    try:
                        pt = trail(at, h)
                    except Exception:
                        pt = None
                    rows.append({"at": at, "tg": tg, "up": up, "upj": upj, "p_raw": f["p_up_usd"], "p_disp": f["p_up_jpy"], "p_trail": pt,
                                 "lo": px < f["q_usd"][0], "hi": px > f["q_usd"][4], "reg": (r.get("regime") or {}).get("state")})
            rows.sort(key=lambda x: x["at"])

            def units(rs):
                if not rs:
                    return 0.0
                if h == "1d":
                    return float(len({x["tg"].astimezone(JST).date() for x in rs}))
                return round((max(x["at"] for x in rs) - min(x["at"] for x in rs)).total_seconds() / 86400 / 7, 2)
            o = {"n": len(rows), "units": units(rows), "units_basis": "目標日の数" if h == "1d" else "予想を作った期間÷7日（重ならない週の数）",
                 "reference_only": units(rows) < 20}
            if rows:
                dr = [(x["p_raw"] - x["up"]) ** 2 - 0.25 for x in rows]
                o["raw"] = {"brier": float(np.mean(dr)) + 0.25, "diff": float(np.mean(dr)), "ci95": boot(dr, blk)}
                rj = [x for x in rows if x["upj"] is not None]
                if rj:
                    dj = [(x["p_disp"] - x["upj"]) ** 2 - 0.25 for x in rj]
                    o["disp_jpy"] = {"n": len(rj), "brier": float(np.mean(dj)) + 0.25, "diff": float(np.mean(dj)), "ci95": boot(dj, blk),
                                     "p_mean": float(np.mean([x["p_disp"] for x in rj])), "up_rate": float(np.mean([x["upj"] for x in rj]))}
                rt = [x for x in rows if x["p_trail"] is not None]
                if rt:
                    o["trail_base"] = {"n": len(rt), "brier": float(np.mean([(x["p_trail"] - x["up"]) ** 2 for x in rt])), "p_mean": float(np.mean([x["p_trail"] for x in rt]))}
                dn = [x for x in rows if x["reg"] == "下落"]
                o["down"] = {"n": len(dn), "units": units(dn), "lo": (float(np.mean([x["lo"] for x in dn])) if dn else None),
                             "hi": (float(np.mean([x["hi"] for x in dn])) if dn else None)}
            res[h] = o
        return {"by_h": res, "scope": "定時（朝・夜）の回のみ", "basis": "ブライア−0.25 の95%区間は、1日後は1日（4回）、1週間後は7日（28回）をひとまとまりにしたブロックブートストラップ。直前の傾向の基準は予想時点で分かる情報だけ"}
    except Exception as ex:
        return {"error": str(ex)[:200]}


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
        # --paginate は次ページのURLが repositories/{id}/... になり、プロキシ経由で403になる（2026-11-17）。ページ番号で自前に回す
        since = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines, out = [], None
        for pg in range(1, 21):
            out = subprocess.run(["gh", "api", "repos/%s/actions/runs?per_page=100&page=%d&created=>=%s" % (REPO, pg, since),
                                  "--jq", ".workflow_runs[] | [.name, .conclusion, .run_started_at, .updated_at] | @tsv"],
                                 capture_output=True, text=True, timeout=120)
            if out.returncode != 0:
                break
            got = [x for x in out.stdout.strip().splitlines() if x]
            lines += got
            if len(got) < 100:
                break
        if out.returncode != 0:
            return {"ok": False, "error": out.stderr.strip()[:200]}
        tot, n, fail, canc, by = 0.0, 0, 0, 0, {}
        for line in lines:
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
    PR = Prices()
    sc = score(recs, PR, now, start)
    sx = score_extra(recs, PR, now)
    sc["extra"] = sx
    ops = operations(recs, start, now)
    gaps = data_gaps(recs, start, now)
    act = actions_minutes(start, now)
    gcp = gcp_cost(start, now)
    keys = keys_due(now)
    ch = changes(recs, start, now)
    nw = next_week(recs, now)
    imp = improvements(sc, ops, gaps, recs, act)
    # 範囲の監視（2026-11-25 提案4）：下落レジームで1週間後の80%範囲が下に外れた割合が10%を超えたら拾う。過去（2020〜）の平均は約8%、急落の年（2022・2026）は12%台
    try:
        dn = ((sx.get("by_h") or {}).get("1w") or {}).get("down") or {}
        if dn.get("n", 0) >= 8 and dn.get("lo") is not None and dn["lo"] > 0.10:
            imp.append("下落レジームで1週間後の80%%範囲が下に外れた割合が%.0f%%（目安10%%、%d件・実質%.1f週%s）。レジーム別の補正は過去に根拠なし。急落の年の傾向として監視（実施は🔬検証・改良チャットで確かめてから）" % (dn["lo"] * 100, dn["n"], dn["units"], "・参考値" if dn["units"] < 20 else ""))
    except Exception:
        pass

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
    # 追加の採点（2026-11-25 🔬検証・改良の提案1〜4）
    if isinstance(sx, dict) and sx.get("by_h"):
        def f2(v): return "—" if v is None else "%.4f" % v
        def ci(c): return "" if not c else "（%+.3f〜%+.3f）" % (c[0], c[1])
        b1, b2 = sx["by_h"]["1d"], sx["by_h"]["1w"]
        def cell(b, k):
            x = b.get(k)
            return "—" if not x else "%+.4f%s" % (x["diff"], ci(x["ci95"]) if x["ci95"] else "（区間は件数不足）")
        M += ["### 追加の採点（定時の朝・夜のみ。🔬検証・改良 2026-11-25）", "",
              "| 項目 | 1日後 | 1週間後 |", "| --- | --- | --- |",
              "| 件数（実質の単位） | %d件（%.0f日） | %d件（%.1f週） |" % (b1["n"], b1["units"], b2["n"], b2["units"]),
              "| 補正前の確率（ドルの結果）ブライア−0.25（95%区間） | " + cell(b1, "raw") + " | " + cell(b2, "raw") + " |",
              "| 画面の確率（補正後・円の結果）ブライア−0.25（95%区間） | " + cell(b1, "disp_jpy") + " | " + cell(b2, "disp_jpy") + " |",
              "| 直前の傾向を使う基準のブライア（「常に50%」は0.25） | " + f2((b1.get("trail_base") or {}).get("brier")) + " | " + f2((b2.get("trail_base") or {}).get("brier")) + " |",
              "| 下落レジームの80%%範囲の下外れ（件数） | %s（%d件） | %s（%d件） |" % (pc((b1.get("down") or {}).get("lo")), (b1.get("down") or {}).get("n", 0), pc((b2.get("down") or {}).get("lo")), (b2.get("down") or {}).get("n", 0)), "",
              "実質の単位が20未満は参考値。差の95%区間が0をまたぐ間は、基準との差は判定できない（設計書 追補 第19章）。一方向の相場では、低め（または高め）の一定の確率が有利になるだけで、力の証拠にはならない。", ""]
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
              "score": {"week": w, "all": al}, "score_extra": sx, "operations": {k: ops[k] for k in ("planned", "done", "missed")},
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
