"""予想エンジン（段階1）の実行。

使い方：  python3 engine/run.py --mode morning|night [--now 2026-11-07T20:00:00+09:00] [--run-id ...]
出力：
  forecasts/YYYY-MM-DD_HHMM_<mode>.json   予想の記録（上書きしない。採点に使う）
  forecasts/latest.json                    最新の予想
  engine/out/update.json                   ダッシュボードの updates/<run_id> に書く1件分
  engine/out/summary.txt                   チャットで報告する要点
リポジトリの data/ と state/ は読むだけで、書き込まない。
"""
import argparse, json, math, os, sys, time, hashlib
from datetime import datetime, timedelta, timezone
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import inputs as I
import models as M

JST = I.JST
ENGINE_VERSION = "1.0.0"
Q = (10, 25, 50, 75, 90)


def iso(d):
    return None if d is None else d.astimezone(JST).isoformat(timespec="minutes")


def year_end(now):
    ye = datetime(now.year, 12, 31, 23, 59, tzinfo=JST)
    if (ye - now).days < 3:
        ye = datetime(now.year + 1, 12, 31, 23, 59, tzinfo=JST)
    return ye


def coverage_backtest(close, cfg, days=730):
    """モデルCの範囲（50%・80%）に、実際の価格が入った割合（過去 days 日、日足）。"""
    lr = np.diff(np.log(close.values))
    lam, w, b = cfg["vol"]["ewma_lambda"], cfg["vol"]["window_days"], cfg["vol"]["blend_ewma"]
    v = lr[:30].var(); ew = np.empty(lr.size)
    for i, x in enumerate(lr):
        ew[i] = math.sqrt(v); v = lam * v + (1 - lam) * x * x   # ew[i]: i日目より前の情報だけ
    out = {}
    n = lr.size
    for h in (1, 7, 30):
        hit50 = hit80 = cnt = 0
        for t in range(max(w + 1, n - days - h), n - h + 1):
            sig = b * ew[t] + (1 - b) * float(np.std(lr[t - w:t], ddof=1))
            z = lr[t:t + h].sum() / (sig * math.sqrt(h))
            cnt += 1; hit50 += abs(z) <= 0.6745; hit80 += abs(z) <= 1.2816
        out[str(h)] = {"n": cnt, "in50": hit50 / cnt, "in80": hit80 / cnt}
    return out


def summarize(samples_usd, samples_jpy, s0_usd, s0_jpy):
    return {
        "p_up_jpy": float((samples_jpy > s0_jpy).mean()),
        "p_up_usd": float((samples_usd > s0_usd).mean()),
        "q_jpy": [float(np.percentile(samples_jpy, q)) for q in Q],
        "q_usd": [float(np.percentile(samples_usd, q)) for q in Q],
    }


def _round(o):
    """ダッシュボードに書く値を有効数字5桁に丸める（書き込みを小さくするため。記録ファイルは丸めない）。"""
    if isinstance(o, float):
        return float("%.5g" % o) if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="morning", choices=["morning", "night", "manual"])
    ap.add_argument("--now", default=None)
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args()
    t0 = time.time()
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    evdoc = json.load(open(os.path.join(HERE, "events.json")))
    now = datetime.fromisoformat(a.now) if a.now else datetime.now(JST)
    now = now.astimezone(JST).replace(second=0, microsecond=0)
    run_id = a.run_id or now.strftime("%Y%m%d-%H%M") + "-" + a.mode
    utcnow = now.astimezone(timezone.utc)

    # ---------- 入力 ----------
    usd, jpy, fx, fallbacks = I.current_prices(ROOT, utcnow, cfg)
    close = I.load_daily_usd(ROOT)
    vol = I.realized_vol(close, cfg)
    fxv = I.fx_vol(ROOT, cfg)
    reg = I.regime(close, usd["price"], cfg)
    fed = I.fed_market(ROOT)
    der = I.derivatives(ROOT)
    mac = I.macro(ROOT, utcnow)
    fst = I.fetch_status(ROOT, utcnow)

    ye = year_end(now)
    hz = []
    for h in cfg["horizons"]:
        days = h["days"] if "days" in h else (ye - now).total_seconds() / 86400
        hz.append({**h, "steps": max(1, int(round(days))), "target": iso(now + timedelta(days=days)) if "days" in h else iso(ye)})
    T = max(x["steps"] for x in hz)
    h_idx = [x["steps"] - 1 for x in hz]

    events_c, skipped = M.compile_events(evdoc["events"], now, T, fed)
    vmult = M.step_vol_mult(now, T, cfg["vol"]["weekend_mult"])
    target = vol["daily"]
    seed = cfg["seed"]
    base_sig, ev_var = M.calibrate_base_sig(seed + 7, T, target, vmult, events_c, cfg["idiosyncratic_jumps"], cfg["vol"]["base_floor_ratio"])

    N = cfg["paths"]["night" if a.mode == "night" else "morning"]
    CH = cfg["chunk"]
    wA, wC = cfg["weights"]["A"], cfg["weights"]["C"]
    nA = int(round(N * wA / (wA + wC))); nC = N - nA
    s0u, s0j, fx0 = usd["price"], jpy["price"], jpy["price"] / usd["price"]

    def run_model(kind, n_total, seed0):
        U, J, F, fan = [], [], {}, []
        left, k = n_total, 0
        while left > 0:
            n = min(CH, left)
            if kind == "A":
                lx, lfx, fac = M.model_a_chunk(seed0 + k, n, T, base_sig, vmult, events_c, cfg["idiosyncratic_jumps"], fxv, cfg["vol"]["t_df"], h_idx)
                for kk, vv in fac.items(): F.setdefault(kk, []).append(vv)
            else:
                lx, lfx = M.model_c_chunk(seed0 + k, n, T, target, fxv, cfg["vol"]["t_df"])
            pu = s0u * np.exp(lx); pj = pu * fx0 * np.exp(lfx)
            U.append(pu[:, h_idx]); J.append(pj[:, h_idx]); fan.append(pj[::20])
            left -= n; k += 1
        return np.concatenate(U), np.concatenate(J), {kk: np.concatenate(v) for kk, v in F.items()}, np.concatenate(fan)

    UA, JA, FA, fanA = run_model("A", nA, seed * 10 + 1000)
    UC, JC, _, fanC = run_model("C", nC, seed * 10 + 5000)
    UE, JE, fanE = np.concatenate([UA, UC]), np.concatenate([JA, JC]), np.concatenate([fanA, fanC])

    bt = coverage_backtest(close, cfg)
    forecasts = []
    for i, h in enumerate(hz):
        e = summarize(UE[:, i], JE[:, i], s0u, s0j)
        sa = summarize(UA[:, i], JA[:, i], s0u, s0j)
        sc = summarize(UC[:, i], JC[:, i], s0u, s0j)
        lo = min(sa["p_up_jpy"], sc["p_up_jpy"]); hi = max(sa["p_up_jpy"], sc["p_up_jpy"])
        lo = min(lo, e["p_up_jpy"] - 0.03); hi = max(hi, e["p_up_jpy"] + 0.03)
        spread = abs(sa["p_up_jpy"] - sc["p_up_jpy"])
        reasons = ["運用中の採点がまだ0件（第9章の検証は段階3）", "モデルAのイベントの確率と影響の多くが「判断」"]
        btk = str(h["steps"]) if str(h["steps"]) in bt else None
        if btk:
            reasons.append("モデルCの範囲の過去%d日の的中率：50%%範囲 %.0f%%・80%%範囲 %.0f%%" % (bt[btk]["n"], bt[btk]["in50"] * 100, bt[btk]["in80"] * 100))
        if spread > 0.10:
            reasons.insert(0, "モデル間の差が大きい（%.0fポイント）" % (spread * 100))
        forecasts.append({"id": h["id"], "label": h["label"], "target": h["target"], "steps": h["steps"],
                          "p_up_jpy": e["p_up_jpy"], "p_up_usd": e["p_up_usd"], "p_band": [lo, hi],
                          "q_jpy": e["q_jpy"], "q_usd": e["q_usd"],
                          "range50_jpy": [e["q_jpy"][1], e["q_jpy"][3]], "range80_jpy": [e["q_jpy"][0], e["q_jpy"][4]],
                          "range50_usd": [e["q_usd"][1], e["q_usd"][3]], "range80_usd": [e["q_usd"][0], e["q_usd"][4]],
                          "confidence": "低", "confidence_reasons": reasons,
                          "models": {"A": sa, "C": sc}, "model_spread": spread})

    # 扇形のグラフ用（円・日ごとの分位）
    fan = {"days": [iso(now + timedelta(days=s + 1))[:10] for s in range(T)],
           "q": [[round(float(np.percentile(fanE[:, s], q)), 2) for q in Q] for s in range(T)]}

    # イベントごとの影響（モデルAの道筋を結果ごとに分けたときの、年末に上がっている確率と中央値）
    yi = len(hz) - 1
    ev_out = []
    for ev in events_c:
        f = FA.get(ev["id"])
        rows = []
        for oi, o in enumerate(ev["outcomes"]):
            m = f == oi
            if m.mean() < 0.005:
                rows.append({"label": o["label"], "share": float(m.mean())}); continue
            rows.append({"label": o["label"], "share": float(m.mean()), "p_up_ye": float((JA[m, yi] > s0j).mean()),
                         "median_ye_jpy": float(np.median(JA[m, yi]))})
        pu = [r["p_up_ye"] for r in rows if "p_up_ye" in r]
        swing = (max(pu) - min(pu)) if len(pu) > 1 else 0.0
        tm = ev["timing"]
        ev_out.append({"id": ev["id"], "name": ev["name"], "kind": ev["kind"],
                       "when": tm.get("at", "〜".join(tm["window"]) if "window" in tm else "期間中ずっと"),
                       "when_basis": tm.get("at_basis"), "p_basis": ev["p_used_basis"], "p_reason": ev.get("p_reason"),
                       "impact_basis": ev.get("impact_basis"), "outcomes": rows, "swing_p_up_ye": swing,
                       "impact_size": "大" if swing >= 0.25 else "中" if swing >= 0.10 else "小"})
    ev_out.sort(key=lambda x: x["when"])

    elapsed = time.time() - t0
    in_hash = hashlib.sha256(json.dumps([usd["price"], jpy["price"], vol["daily"], fed and fed["p_hike"], evdoc["updated_at"], ENGINE_VERSION, N], default=str).encode()).hexdigest()[:12]
    rec = {
        "run_id": run_id, "at": iso(now), "mode": a.mode, "engine_version": ENGINE_VERSION, "input_hash": in_hash,
        "seed": seed, "paths": {"A": nA, "C": nC}, "weights": {"A": wA / (wA + wC), "C": wC / (wA + wC)},
        "spot": {"usd": s0u, "jpy": s0j, "usd_at": iso(usd["at"]), "jpy_at": iso(jpy["at"]), "usd_source": usd["source"], "jpy_source": jpy["source"]},
        "vol": {**vol, "base_sig_daily": base_sig, "event_var_daily": ev_var, "fx_daily": fxv,
                "basis": "代用：オプションの予想変動率（Deribit）が未取得のため、実現変動率で代用"},
        "fed": fed, "regime": reg, "forecasts": forecasts, "events": ev_out, "events_skipped": skipped,
        "backtest_c": bt, "elapsed_sec": round(elapsed, 1),
        "assumptions": ["予想の計算はこのプログラムで行い、AIは数字を作らない",
                        "モデルCはオプション市場の代わりに実現変動率を使う（方向の偏りなし）",
                        "モデルAのイベントの確率・影響は、12月FOMCの利上げ確率（予測市場）以外は「判断」（既存の100万回試算の設定を引き継ぎ）",
                        "段階1のため、モデルB・D・Eは使っていない"],
    }
    os.makedirs(os.path.join(ROOT, "forecasts"), exist_ok=True)
    fn = os.path.join(ROOT, "forecasts", now.strftime("%Y-%m-%d_%H%M") + "_" + a.mode + ".json")
    json.dump(rec, open(fn, "w"), ensure_ascii=False, indent=1)
    json.dump(rec, open(os.path.join(ROOT, "forecasts", "latest.json"), "w"), ensure_ascii=False, indent=1)

    # ---------- ダッシュボードの1件分 ----------
    j = I.load_daily_jpy(ROOT)
    prices = [[str(d), round(float(c), 2)] for d, c in zip(j.date.iloc[-30:], j.close.iloc[-30:])]
    upd = {
        "run_id": run_id, "at": iso(now), "at_utc": utcnow.isoformat(), "mode": a.mode, "engine_version": ENGINE_VERSION,
        "status": {"price_jpy": s0j, "price_usd": s0u, "chg24h_usd": usd.get("chg24h"), "chg_prev_close_jpy": jpy["chg_prev_close"],
                   "prev_close_date": jpy["prev_close_date"], "regime": reg["state"], "regime_detail": reg,
                   "usd_at": iso(usd["at"]), "jpy_at": iso(jpy["at"]), "usd_source": usd["source"], "jpy_source": jpy["source"],
                   "fx": fx0, "fx_source": fx["source"]},
        "fallbacks": fallbacks,
        "forecast": {"items": [{k: v for k, v in f.items() if k != "models"} | {"pA": f["models"]["A"]["p_up_jpy"], "pC": f["models"]["C"]["p_up_jpy"]} for f in forecasts],
                     "vol_annual": vol["annual"], "vol_basis": rec["vol"]["basis"], "weights": rec["weights"], "paths": rec["paths"],
                     "seed": seed, "input_hash": in_hash, "fan": fan, "assumptions": rec["assumptions"]},
        "derivatives": der, "macro": {**mac, "fed": fed, "fedwatch": None},
        "events": [{k: v for k, v in e.items() if k not in ("p_reason", "when_basis", "impact_basis")} for e in ev_out], "events_skipped": skipped,
        "news": None,
        "verification": {"backtest_c": bt, "scorecard": None, "note": "運用中の予想の採点は、期限が来た分から週次で行う（段階3で本格化）"},
        "fetchers": fst["fetchers"], "freshness": [{k: v for k, v in x.items() if k != "age_min"} for x in fst["freshness"]], "state_updated_at": fst["state_updated_at"],
        "prices_jpy": prices,
        "record_file": os.path.relpath(fn, ROOT), "elapsed_sec": round(elapsed, 1),
    }
    upd = _round(upd)
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    s = json.dumps(upd, ensure_ascii=False, separators=(",", ":"), default=str)
    open(os.path.join(HERE, "out", "update.json"), "w").write(s)

    # ---------- 要点 ----------
    L = ["XRP予想（%s・%s）" % (iso(now), a.mode),
         "現在値：%.2f円（%s）／$%.4f（%s）、相場の状態：%s" % (s0j, jpy["source"], s0u, usd["source"], reg["state"])]
    for f in forecasts:
        L.append("%s：上がる確率 約%.0f%%（%.0f〜%.0f%%）、50%%範囲 %.1f〜%.1f円、80%%範囲 %.1f〜%.1f円、信頼度 %s" % (
            f["label"], f["p_up_jpy"] * 100, f["p_band"][0] * 100, f["p_band"][1] * 100,
            f["range50_jpy"][0], f["range50_jpy"][1], f["range80_jpy"][0], f["range80_jpy"][1], f["confidence"]))
    if fed:
        L.append("12月FOMCの利上げ確率（予測市場）：%.0f%%" % (fed["p_hike"] * 100))
    if fallbacks:
        L.append("代わりの取得先：" + "、".join(x["item"] + "→" + x["used"] for x in fallbacks))
    L.append("変動率（年率）：%.0f%%（実現変動率で代用）、計算 %.1f秒、記録 %s、ダッシュボード用 %d KB" % (vol["annual"] * 100, elapsed, os.path.relpath(fn, ROOT), len(s.encode()) // 1024))
    open(os.path.join(HERE, "out", "summary.txt"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
