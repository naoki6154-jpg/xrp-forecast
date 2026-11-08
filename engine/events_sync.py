"""モデルAのイベント表を、毎回の実行時に組み立てる（段階2・設計書 第5章・第7章）。

優先順位（設計書 第5章）：①市場が付けている確率 ②過去事実データベースの実績 ③判断。
- 確率・日時：📰イベント台帳（ledger/calendar.json）に「市場」「実績」の確率があれば、engine/events.json の「判断」より優先する。
  台帳にしかない経済指標（PCEなど、影響「中」以上）は足す。発表済みで結果（ledger/results.csv）があるものは確定扱い。
- 影響の大きさ（経済指標・金融政策）：facts/reactions.csv（2018年6月以降、発表日のXRPの1日の値動き）から
  「発表日にふだんより余分に動く大きさ」と、予想との差（facts/event_values.csv の surprise）ごとの平均の差を測り、
  件数が少ないほど0に寄せる（縮小推定：n/(n+10)）。結果ごとの値動きは「市場の織り込みを外れた分だけ動く」形
  （予想どおりなら0、外れた分に比例。期待値が0になる）にする。向き（タカ派の結果は下げ）は理屈で決める。
- 上の2つで決められないもの（地政学・需給・株の局面など）は engine/events.json の「判断」のまま使う。
入力は読むだけ（ledger/・facts/ は他のチャットの担当）。
"""
import json, math, os
import numpy as np
import pandas as pd

SHRINK_N = 10
# 経済指標・金融政策：結果の向き（+1：XRPに追い風＝ハト派寄り、−1：向かい風＝タカ派寄り）
DIRECTION = {
    "下振れ": 1, "予想通り": 0, "上振れ": -1, "弱い": 1, "強い": -1,
    "0.1%以下": 1, "0.2%": 0, "0.3%以上": -1,
    "利下げ": 1, "据え置き": 0, "利上げ": -1,   # 日銀は円高→円建てで下げ（fx）として別に扱う
    "ハト派寄り": 1, "中立": 0, "タカ派寄り": -1,
}
SUBCAT = {"cpi": "cpi", "nfp": "nfp", "pce": "pce", "fomc": "fomc"}
SURPRISE_METRIC = {"cpi": ["コアCPI 前年比", "CPI 前年比"], "nfp": ["非農業部門雇用者数 前月差"], "pce": ["コアPCE 前年比", "PCE 前年比"]}
SURPRISE_SIGN = {"cpi": -1, "pce": -1, "nfp": -1}   # 予想より高い（surprise>0）＝タカ派＝向き−1
# 台帳の結果の名前 → engine の結果の名前（順番どおりに対応）
LABEL_MAP = {"iran": ["和平合意", "現状維持", "戦闘再拡大"]}


def _subcat(eid):
    for k in SUBCAT:
        if eid.startswith(k) and not eid.startswith("fomc_minutes"):
            return k
    return None


def reaction_stats(root):
    """経済指標ごとの、発表日の余分な値動き（標準偏差）と、予想との差の向きごとの平均。"""
    r = pd.read_csv(os.path.join(root, "facts/reactions.csv"))
    v = pd.read_csv(os.path.join(root, "facts/event_values.csv"))
    c = pd.read_csv(os.path.join(root, "data/raw/prices/binance_xrp_usdt_1d.csv"))
    s = pd.Series(c.close.astype(float).values, index=pd.to_datetime(c.date))
    lr = np.log(s).diff().dropna()
    normal_mad = float(lr.abs().median() * 100)
    out = {"_normal_median_abs_pct": normal_mad, "_basis": "facts/reactions.csv の1日（2018-06以降）と Binance XRP/USDT 日足"}
    d1 = r[(r.horizon == "1日") & (r.event_time_utc >= "2018-06")]
    for sub in SUBCAT.values():
        g = d1[d1.subcategory == sub].dropna(subset=["xrp_ret_pct"])
        if not len(g):
            continue
        mad = float(g.xrp_ret_pct.abs().median())
        excess = math.sqrt(max(mad ** 2 - normal_mad ** 2, 0.0)) * 1.4826   # 中央値の絶対値→標準偏差に換算
        st = {"n": int(len(g)), "median_abs_pct": mad, "excess_sd_pct": excess, "up_share": float((g.xrp_ret_pct > 0).mean()),
              "first": str(g.event_time_utc.min())[:10], "last": str(g.event_time_utc.max())[:10]}
        mets = SURPRISE_METRIC.get(sub)
        if mets:
            sv = v[v.surprise.notna() & v.metric.isin(mets)].copy()
            sv["rank"] = sv.metric.map({m: i for i, m in enumerate(mets)})
            sv = sv.sort_values("rank").drop_duplicates("event_id")
            m = sv.merge(g[["event_id", "xrp_ret_pct"]], on="event_id")
            if len(m) >= 4:
                dirn = np.sign(m.surprise) * SURPRISE_SIGN[sub]   # +1：ハト派寄りの外れ
                grp = {}
                for k, nm in ((1, "dove"), (0, "inline"), (-1, "hawk")):
                    x = m.xrp_ret_pct[dirn == k]
                    grp[nm] = {"n": int(len(x)), "mean_pct": float(x.mean()) if len(x) else None}
                base = float(m.xrp_ret_pct.mean())
                beta = []
                for nm, k in (("dove", 1), ("hawk", -1)):
                    if grp[nm]["n"]:
                        sh = grp[nm]["n"] / (grp[nm]["n"] + SHRINK_N)
                        beta.append(k * (grp[nm]["mean_pct"] - base) * sh)
                st["surprise"] = {"groups": grp, "n": int(len(m)), "metric": "/".join(mets),
                                  "beta_pct": float(np.mean(beta)) if beta else None}
        out[sub] = st
    return out


def _impact_effects(sub, labels, probs, stats):
    """経済指標の結果ごとの値動き：jump_k = β×(向き_k − Σp×向き)。β は実績から、ノイズは発表日の余分な値動き。"""
    st = stats.get(sub)
    if not st:
        return None, None
    beta = None
    if st.get("surprise") and st["surprise"].get("beta_pct") is not None and st["surprise"]["beta_pct"] > 0:
        beta = st["surprise"]["beta_pct"] / 100
        how = "予想との差の向きごとの平均の差（%d件、件数が少ないので0に寄せた）" % st["surprise"]["n"]
    else:
        beta = st["excess_sd_pct"] / 100
        how = "予想との差の記録が足りないため、発表日の余分な値動きの大きさ（%d件）を外れたときの動きとした" % st["n"]
    dirs = [DIRECTION.get(l, 0) for l in labels]
    ed = sum(p * d for p, d in zip(probs, dirs))
    noise = max(st["excess_sd_pct"] / 100, 0.004)
    effs = [[{"kind": "jump", "day": 0, "amt": round(beta * (d - ed), 5), "noise": round(noise, 5)}] for d in dirs]
    effs.append(beta)
    reason = ("facts/reactions.csv：発表日のXRPの1日の値動き（%s〜%s、%d件）の絶対値の中央値 %.2f%%（ふだんの日 %.2f%%）。"
              "外れたときの動き β=%.2f%%：%s。予想どおりの分は織り込み済みとして、外れた分だけ動く形（期待値0）。向き（タカ派寄りは下げ）は理屈"
              % (st["first"], st["last"], st["n"], st["median_abs_pct"], stats["_normal_median_abs_pct"], beta * 100, how))
    return effs, reason


def build(root, evdoc, now_jst):
    """engine/events.json と台帳・過去事実から、今回使うイベント表を作る。戻り値：(events, notes)"""
    base = {e["id"]: json.loads(json.dumps(e)) for e in evdoc["events"]}
    notes = {"changes": [], "stats": None, "ledger_updated_at": None}
    try:
        cal = json.load(open(os.path.join(root, "ledger/calendar.json")))
        notes["ledger_updated_at"] = cal.get("updated_at")
    except Exception:
        cal = {"events": []}
        notes["changes"].append("台帳（ledger/calendar.json）が読めないため、engine/events.json のまま")
    try:
        res = pd.read_csv(os.path.join(root, "ledger/results.csv"))
    except Exception:
        res = pd.DataFrame(columns=["calendar_id", "outcome_label"])
    try:
        stats = reaction_stats(root)
        notes["stats"] = stats
    except Exception as ex:
        stats = {}
        notes["changes"].append("過去の反応の統計が作れない（%s）ため、影響は判断のまま" % ex)

    # 方針：基調の下げ（trend_drift）は判断なので使わない。トレンドはモデルB（過去の統計）が扱う
    if "trend_drift" in base:
        base.pop("trend_drift")
        notes["changes"].append("「下落トレンドの基調」（判断で55日−3%）は外した。トレンドの効果はモデルB（似た過去の局面）が過去の統計から扱う")

    for le in cal.get("events", []):
        eid = le.get("engine_id") or le["id"]
        louts = le.get("outcomes") if isinstance(le.get("outcomes"), list) else []
        lp_basis = le.get("p_basis")
        sub = _subcat(le["id"])
        ev = base.get(eid)
        if ev is None:
            # 台帳にしかないもの：経済指標で影響「中」以上、確率が市場か実績、日時が決まっているものだけ足す
            if not (sub and le.get("impact") in ("大", "中") and lp_basis in ("市場", "実績") and louts and "〜" not in str(le.get("when"))):
                continue
            ev = {"id": eid, "name": le["name"], "kind": le.get("kind", "経済指標"), "impact_size": le.get("impact"),
                  "timing": {"at": le["when"], "at_basis": le.get("time_basis")},
                  "outcomes": [{"label": o["label"], "p": o["p"], "effects": []} for o in louts]}
            base[eid] = ev
            notes["changes"].append("%s を台帳から追加（確率：%s）" % (le["name"], lp_basis))
        # 日時：台帳が確定させたものを使う
        if le.get("when") and "〜" not in str(le["when"]) and "at" in ev.get("timing", {}) and le["when"] != ev["timing"]["at"]:
            notes["changes"].append("%s の日時を台帳の %s に（%s）" % (ev["name"], le["when"][:16], le.get("time_basis") or ""))
            ev["timing"] = {"at": le["when"], "at_basis": "台帳：" + str(le.get("time_basis") or "")}
        # 確率：市場・実績を優先。判断どうしでも台帳の方が新しい調査なので使う（印は判断のまま）
        if louts and not ev.get("p_from_market"):
            labels = [o["label"] for o in ev["outcomes"]]
            lmap = {o["label"]: o["p"] for o in louts}
            if eid in LABEL_MAP or set(lmap) != set(labels):
                if len(louts) == len(labels):
                    newp = [o["p"] for o in louts]
                elif len(louts) == 2 and len(labels) == 3:     # 成立／不成立 → 可決／再審議へ／廃案（不成立を元の比で分ける）
                    rest = ev["outcomes"][1]["p"] + ev["outcomes"][2]["p"]
                    newp = [louts[0]["p"], louts[1]["p"] * ev["outcomes"][1]["p"] / rest, louts[1]["p"] * ev["outcomes"][2]["p"] / rest]
                else:
                    newp = None
            else:
                newp = [lmap[l] for l in labels]
            if newp:
                old = [o["p"] for o in ev["outcomes"]]
                for o, p in zip(ev["outcomes"], newp):
                    o["p"] = p
                ev["p_basis"] = lp_basis or "判断"
                ev["p_reason"] = "📰イベント台帳（%s）：%s" % (le.get("checked_at") or cal.get("updated_at"), le.get("p_source") or le.get("p_reason") or "")
                if [round(x, 3) for x in old] != [round(x, 3) for x in newp]:
                    notes["changes"].append("%s の確率を台帳の値（%s）に：%s → %s" % (ev["name"], lp_basis, "/".join("%.0f%%" % (x * 100) for x in old), "/".join("%.0f%%" % (x * 100) for x in newp)))
        # 発表済みの結果があれば確定
        rr = res[res.calendar_id == le["id"]] if len(res) else res
        if len(rr) and isinstance(rr.outcome_label.iloc[-1], str):
            ev["fixed_outcome"] = rr.outcome_label.iloc[-1]

    # 影響の大きさ：経済指標・金融政策（米国）は実績から
    for eid, ev in base.items():
        sub = _subcat(eid)
        if not sub or not stats:
            continue
        labels = [o["label"] for o in ev["outcomes"]]
        probs = [o["p"] for o in ev["outcomes"]]
        effs, reason = _impact_effects(sub, labels, probs, stats)   # FOMCの市場の確率は実行時に入るので、その時に期待値0に直す
        if not effs:
            continue
        beta = effs.pop()
        for o, ef in zip(ev["outcomes"], effs):
            keep = [x for x in o.get("effects", []) if x["kind"] == "fx"]   # ドル円への影響は残す
            o["effects"] = ef + keep
        ev["impact_basis"] = "実績"
        ev["impact_reason"] = reason
        ev["neutral_jump"] = {"beta": beta, "dirs": [DIRECTION.get(l, 0) for l in labels]}
    evs = sorted(base.values(), key=lambda e: e["timing"].get("at") or (e["timing"].get("window") or ["0"])[0])
    return evs, notes


def renormalize_neutral(ev, probs):
    """実行時に確率が決まったイベント（FOMCの市場の確率など）で、結果ごとの値動きを期待値0に直す。models.compile_events から呼ぶ。"""
    nj = ev.get("neutral_jump")
    if not nj or len(nj["dirs"]) != len(probs):
        return
    ed = sum(p * d for p, d in zip(probs, nj["dirs"]))
    for o, d in zip(ev["outcomes"], nj["dirs"]):
        if o["effects"] and o["effects"][0]["kind"] == "jump":
            o["effects"][0]["amt"] = round(nj["beta"] * (d - ed), 5)


if __name__ == "__main__":
    import sys
    from datetime import datetime, timezone, timedelta
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    evdoc = json.load(open(os.path.join(root, "engine/events.json")))
    evs, notes = build(root, evdoc, datetime.now(timezone(timedelta(hours=9))))
    for c in notes["changes"]:
        print("・", c)
    for e in evs:
        print(e["id"], e.get("p_basis"), e.get("impact_basis"), [(o["label"], round(o["p"], 3), [x.get("amt") for x in o["effects"]]) for o in e["outcomes"]])
    print(json.dumps({k: v for k, v in notes["stats"].items()}, ensure_ascii=False, indent=1)[:2500])
