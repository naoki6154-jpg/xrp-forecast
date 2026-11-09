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
import models_bd as BD
import indicators as IND
import events_sync as ES
import weekly as WK
import model_e as ME
import alerts as AL
import technicals as TA
import chartpat as CP
import pandas as pd

JST = I.JST
ENGINE_VERSION = "1.3.0"
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

    ev_list, ev_notes = ES.build(ROOT, evdoc, now)
    events_c, skipped = M.compile_events(ev_list, now, T, fed)
    vmult = M.step_vol_mult(now, T, cfg["vol"]["weekend_mult"])
    # ---------- 変動の大きさ：Deribit の予想変動率（満期ごと）と実現変動率 ----------
    term = BD.iv_term(os.path.join(ROOT, "data/clean/xrp_atm_iv.csv"), utcnow)
    if term:
        iv30 = BD.iv_at(term, 30)
        sigC = BD.iv_step_sigmas(term, T)
        target = 0.5 * vol["daily"] + 0.5 * iv30 / math.sqrt(365)
        vol_basis = "モデルC：Deribitの予想変動率（満期ごと、%s 取得）。モデルA・B・Dの値動きの大きさ：予想変動率（30日）と実現変動率の平均" % term["fetched_at"][:16]
    else:
        iv30 = None
        sigC = vol["daily"]
        target = vol["daily"]
        vol_basis = "代用：Deribitの予想変動率が取れない・2日より古いため、実現変動率で代用"
        fallbacks.append({"item": "モデルCの変動率", "used": "実現変動率", "reason": "Deribitの予想変動率が取れない・2日より古い"})
    seed = cfg["seed"]
    base_sig, ev_var = M.calibrate_base_sig(seed + 7, T, target, vmult, events_c, cfg["idiosyncratic_jumps"], cfg["vol"]["base_floor_ratio"])

    N = cfg["paths"]["night" if a.mode == "night" else "morning"]
    CH = cfg["chunk"]
    W = cfg["weights"]; MS = [m for m in "ABCDT" if W.get(m)]
    wsum = sum(W[k] for k in MS)
    nA = int(round(N * W["A"] / wsum)); nB = int(round(N * W["B"] / wsum)); nD = int(round(N * W["D"] / wsum)); nT = int(round(N * W.get("T", 0) / wsum))
    nC = N - nA - nB - nD - nT
    s0u, s0j, fx0 = usd["price"], jpy["price"], jpy["price"] / usd["price"]
    dl_idx = [x["steps"] - 1 for x in hz if x["id"] in ("1w", "1m", "ye")]

    LIM = cfg.get("strategy", {}).get("limits", [1.05, 1.10, 1.15, 1.20, 1.30])
    STP = cfg.get("strategy", {}).get("stops", [0.90, 0.85, 0.80])

    def first_touch(pj, ratios, up):
        out = np.empty((pj.shape[0], len(ratios)), np.int16)
        for i, r_ in enumerate(ratios):
            hit = pj >= s0j * r_ if up else pj <= s0j * r_
            out[:, i] = np.where(hit.any(axis=1), hit.argmax(axis=1), 32767)
        return out

    def run_model(kind, n_total, seed0):
        U, J, F, fan, HX, HN, VM, AM, FU, FD = [], [], {}, [], [], [], [], [], [], []
        left, k = n_total, 0
        while left > 0:
            n = min(CH, left)
            if kind == "A":
                lx, lfx, fac = M.model_a_chunk(seed0 + k, n, T, base_sig, vmult, events_c, cfg["idiosyncratic_jumps"], fxv, cfg["vol"]["t_df"], h_idx)
                for kk, vv in fac.items(): F.setdefault(kk, []).append(vv)
            else:
                lx, lfx = M.model_c_chunk(seed0 + k, n, T, sigC, fxv, cfg["vol"]["t_df"])
            pu = s0u * np.exp(lx); pj = pu * fx0 * np.exp(lfx)
            U.append(pu[:, h_idx]); J.append(pj[:, h_idx]); fan.append(pj[::20])
            HX.append(np.maximum.accumulate(pj, axis=1)[:, dl_idx]); HN.append(np.minimum.accumulate(pj, axis=1)[:, dl_idx])
            VM.append(pj.min(axis=1)); AM.append(np.argmin(pj, axis=1).astype(np.int16))
            FU.append(first_touch(pj, LIM, True)); FD.append(first_touch(pj, STP, False))
            left -= n; k += 1
        cat = np.concatenate
        return cat(U), cat(J), {kk: cat(v) for kk, v in F.items()}, cat(fan), {"hx": cat(HX), "hn": cat(HN), "vmin": cat(VM), "amin": cat(AM), "fu": cat(FU), "fd": cat(FD)}

    UA, JA, FA, fanA, XA = run_model("A", nA, seed * 10 + 1000)
    UC, JC, _, fanC, XC = run_model("C", nC, seed * 10 + 5000)
    # モデルB・D：期限ごとの値動きの標本（ドル）。ドル円は別に振る
    fgdf = pd.read_csv(os.path.join(ROOT, "data/raw/sentiment/fear_greed.csv"))
    fgs = pd.Series(fgdf.value.astype(float).values, index=pd.to_datetime(fgdf.date))
    frame = BD.daily_frame(close, fgs)
    steps = [x["steps"] for x in hz]
    rB, infoB, featB = BD.model_b(frame, steps, nB, seed * 10 + 3000, target)
    rD, infoD = BD.model_d(frame, pd.Timestamp(now.date()), steps, nD, seed * 10 + 4000, target)
    rngfx = np.random.default_rng(seed * 10 + 7000)
    sq = np.sqrt(np.array(steps, dtype=np.float32))[None, :]
    UB = s0u * np.exp(rB); JB = UB * fx0 * np.exp(fxv * sq * rngfx.standard_normal(UB.shape).astype(np.float32))
    UD = s0u * np.exp(rD); JD = UD * fx0 * np.exp(fxv * sq * rngfx.standard_normal(UD.shape).astype(np.float32))
    # モデルT：テクニカル指標の状態の組み合わせから（engine/technicals.py）
    tech = None
    try:
        ob = pd.read_csv(os.path.join(ROOT, "data/raw/prices/binance_xrp_usdt_1d.csv"))
        ob.index = pd.to_datetime(ob.date)
        ob = ob[["open", "high", "low", "close", "volume"]].astype(float)

        def fred(n_):
            fp = os.path.join(ROOT, "data/raw/fred/%s.csv" % n_)
            if not os.path.exists(fp):
                return None
            d_ = pd.read_csv(fp)
            return pd.Series(pd.to_numeric(d_["value"], errors="coerce").values, index=pd.to_datetime(d_.date)).dropna()
        TX = TA.compute(ob, fgs, fred("NASDAQCOM"), fred("DTWEXBGS"), fred("VIXCLS"))
        tech = TA.analyze(TX, TX.close)
        rT, infoT = TA.model_t(TX, tech["states"], TX.close, steps, max(nT, 1), seed * 10 + 6000, target)
        rT = rT[:nT]
    except Exception as ex:
        tech = {"error": str(ex)}
        rT, infoT = np.zeros((0, len(steps)), np.float32), []
        nT = 0
    UT = s0u * np.exp(rT); JT = UT * fx0 * np.exp(fxv * sq * rngfx.standard_normal(UT.shape).astype(np.float32))
    UE, JE = np.concatenate([UA, UB, UC, UD, UT]), np.concatenate([JA, JB, JC, JD, JT])
    fanE = np.concatenate([fanA, fanC])

    bt = coverage_backtest(close, cfg)
    # 過去検証（engine/backtest.py が作る）：確率の較正と、範囲の的中率
    try:
        calib = json.load(open(os.path.join(HERE, "calibration.json")))
    except Exception:
        calib = None
    try:
        btx = json.load(open(os.path.join(ROOT, "reports/backtest.json")))
    except Exception:
        btx = None
    # 運用中の予想の採点（期限が来た分。週次の振り返りと同じ方法）
    try:
        sc_live = WK.score(WK.load_records(), WK.Prices(), now, now - timedelta(days=7))
    except Exception as ex:
        sc_live = {"error": str(ex)}
    HK = {"1d": "1d", "1w": "1w", "1m": "1m", "ye": "1m"}

    def calibrate(p, hid):
        if not calib or HK.get(hid) not in calib["horizons"]:
            return None
        c = calib["horizons"][HK[hid]]
        z = c["a"] + c["b"] * math.log(min(max(p, 0.01), 0.99) / (1 - min(max(p, 0.01), 0.99)))
        return 1 / (1 + math.exp(-z))

    def confidence(hid):
        """信頼度の決め方（数字のルール。2026-11-08）。方向（上がる確率）と範囲を分けて付ける。
        方向：運用中の採点が30件以上でブライアスコアが基準（0.25）以下なら「中」、100件以上で基準より0.01以上良ければ「高」。それ以外は「低」。
        範囲：過去検証（混合）の80%範囲の的中率が75〜85%で、運用中の採点（20件以上あれば）も70〜90%なら「中」。"""
        live = (sc_live.get("by_horizon_all") or {}).get(hid, {"n": 0}) if isinstance(sc_live, dict) else {"n": 0}
        n = live.get("n", 0)
        d = "低"
        if n >= 100 and live["brier"] <= 0.24:
            d = "高"
        elif n >= 30 and live["brier"] <= 0.25:
            d = "中"
        bx = (btx or {}).get("results", {}).get(HK.get(hid), {}).get("E")
        r_ = "低"
        if bx and 0.75 <= bx["in80"] <= 0.85 and (n < 20 or 0.70 <= live.get("in80", 0) <= 0.90):
            r_ = "中"
        why = ["方向：運用中の採点 %d件（30件以上でブライアスコアが基準以下なら「中」）" % n]
        if bx:
            why.append("過去検証（%d件、モデルB・C・D・Tの混合）：80%%範囲の的中率 %.0f%%、上がる確率の誤差 %.3f（基準 %.3f）" % (bx["n"], bx["in80"] * 100, bx["brier"], bx["brier_base"]))
        if n:
            why.append("運用中：方向の当たり %.0f%%、80%%範囲 %.0f%%、ブライアスコア %.3f" % (live["dir"] * 100, live["in80"] * 100, live["brier"]))
        return d, r_, why, live
    forecasts = []
    for i, h in enumerate(hz):
        e = summarize(UE[:, i], JE[:, i], s0u, s0j)
        ms_ = {"A": summarize(UA[:, i], JA[:, i], s0u, s0j), "B": summarize(UB[:, i], JB[:, i], s0u, s0j),
               "C": summarize(UC[:, i], JC[:, i], s0u, s0j), "D": summarize(UD[:, i], JD[:, i], s0u, s0j)}
        if nT:
            ms_["T"] = summarize(UT[:, i], JT[:, i], s0u, s0j)
        ps = [v["p_up_jpy"] for v in ms_.values()]
        lo = min(min(ps), e["p_up_jpy"] - 0.03); hi = max(max(ps), e["p_up_jpy"] + 0.03)
        spread = max(ps) - min(ps)
        reasons = ["運用中の採点がまだ少ない（第9章の検証は段階3）", "モデルAのイベントの確率と影響の多くが「判断」",
                   "モデルDは年に1回の件数（%d年分）しかない" % len(infoD[i]["years"])]
        btk = str(h["steps"]) if str(h["steps"]) in bt else None
        if btk:
            reasons.append("実現変動率の範囲の過去%d日の的中率：50%%範囲 %.0f%%・80%%範囲 %.0f%%" % (bt[btk]["n"], bt[btk]["in50"] * 100, bt[btk]["in80"] * 100))
        if spread > 0.10:
            reasons.insert(0, "モデル間の差が大きい（%.0fポイント）" % (spread * 100))
        pc = calibrate(e["p_up_jpy"], h["id"])
        if pc is not None:
            lo = min(lo, pc - 0.03); hi = max(hi, pc + 0.03)
        cdir, crng, cwhy, live = confidence(h["id"])
        reasons = cwhy + reasons
        forecasts.append({"id": h["id"], "label": h["label"], "target": h["target"], "steps": h["steps"],
                          "p_up_jpy": pc if pc is not None else e["p_up_jpy"], "p_up_raw_jpy": e["p_up_jpy"], "p_up_calibrated": pc is not None,
                          "p_up_usd": e["p_up_usd"], "p_band": [lo, hi], "confidence_range": crng, "score_live": live,
                          "q_jpy": e["q_jpy"], "q_usd": e["q_usd"],
                          "range50_jpy": [e["q_jpy"][1], e["q_jpy"][3]], "range80_jpy": [e["q_jpy"][0], e["q_jpy"][4]],
                          "range50_usd": [e["q_usd"][1], e["q_usd"][3]], "range80_usd": [e["q_usd"][0], e["q_usd"][4]],
                          "confidence": cdir, "confidence_reasons": reasons,
                          "models": ms_, "model_spread": spread})

    # ---------- 到達確率（価格×期限）と底値の予想：モデルA・Cの道筋から（B・Dは期限時点の値だけのため） ----------
    HX = np.concatenate([XA["hx"], XC["hx"]]); HN = np.concatenate([XA["hn"], XC["hn"]])
    VMIN = np.concatenate([XA["vmin"], XC["vmin"]]); AMIN = np.concatenate([XA["amin"], XC["amin"]])
    dl = [x for x in hz if x["id"] in ("1w", "1m", "ye")]
    lv = cfg.get("touch_levels", [0.05, 0.10, 0.15, 0.20, 0.30])
    touch = {"deadlines": [{"id": x["id"], "label": x["label"], "target": x["target"]} for x in dl], "rows": []}
    for d in sorted(lv, reverse=True):
        L = s0j * (1 + d)
        touch["rows"].append({"side": "上", "pct": d, "price": L, "p": [float((HX[:, j] >= L).mean()) for j in range(len(dl))]})
    for d in lv:
        L = s0j * (1 - d)
        touch["rows"].append({"side": "下", "pct": -d, "price": L, "p": [float((HN[:, j] <= L).mean()) for j in range(len(dl))]})
    touch["note"] = "期限までに一度でもその価格に届く確率（円）。モデルA・Cの道筋（%s本）から" % f"{len(VMIN):,}"
    deep = VMIN <= s0j * 0.97
    wk = np.minimum(AMIN[deep] // 7, (T - 1) // 7)
    bottom = {"q_min": [float(np.percentile(VMIN, q)) for q in (10, 25, 50)],
              "bands": [{"from": s0j * (1 - b), "to": s0j * (1 - a_), "p": float(((VMIN <= s0j * (1 - a_)) & (VMIN > s0j * (1 - b))).mean())}
                        for a_, b in ((0.0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.30), (0.30, 0.90))],
              "p_no_new_low": float((VMIN > s0j * 0.97).mean()),
              "timing": [{"week": int(w) + 1, "from": iso(now + timedelta(days=int(w) * 7))[:10], "p": float((wk == w).mean())} for w in range(0, (T - 1) // 7 + 1)],
              "until": iso(ye)[:10],
              "note": "年末までの最安値の分布（円）。時期は3%以上下げた道筋の中で、最安値をつけた週の割合。モデルA・Cから"}

    # ---------- 戦略比較（設計書 第8章）：1枚あたり、今の価格を1とした年末までの手取り。保有枚数・取得額は掛けない（画面で本人の値を掛ける） ----------
    FU = np.concatenate([XA["fu"], XC["fu"]]); FD = np.concatenate([XA["fd"], XC["fd"]])
    tend = np.concatenate([JA[:, -1], JC[:, -1]]) / s0j
    st_cfg = cfg.get("strategy", {})
    fee_t = st_cfg.get("fee_taker", 0.0012); fee_m = st_cfg.get("fee_maker", 0.0); slip = st_cfg.get("stop_slippage", 0.01)

    def stat(v, name, kind, detail):
        return {"name": name, "kind": kind, "detail": detail, "mean": float(v.mean()), "median": float(np.median(v)), "p10": float(np.percentile(v, 10)),
                "p90": float(np.percentile(v, 90)), "p_below_now": float((v < 1 - 1e-9).mean()), "p_loss_10": float((v < 0.9).mean())}
    strategies = [stat(np.full(1, 1 - fee_t), "今すぐ売る", "now", "成行で売る（手数料 %.2f%%）" % (fee_t * 100)),
                  stat(tend * (1 - fee_t), "何もしない（年末まで保有）", "hold", "年末の価格で評価")]
    for i, L_ in enumerate(LIM):
        hit = FU[:, i] < 32767
        v = np.where(hit, L_ * (1 - fee_m), tend * (1 - fee_t))
        s_ = stat(v, "指値 +%.0f%%（%.1f円）" % ((L_ - 1) * 100, s0j * L_), "limit", "届けば指値で売る。届かなければ年末まで保有")
        s_["p_fill"] = float(hit.mean()); s_["limit"] = L_
        strategies.append(s_)
        for j, S_ in enumerate(STP):
            if L_ not in (1.10, 1.20) or S_ not in (0.90, 0.85):
                continue
            th, ts_ = FU[:, i], FD[:, j]
            v = np.where(th < ts_, L_ * (1 - fee_m), np.where(ts_ < th, S_ * (1 - slip) * (1 - fee_t), tend * (1 - fee_t)))
            s_ = stat(v, "指値 +%.0f%%＋損切り −%.0f%%" % ((L_ - 1) * 100, (1 - S_) * 100), "limit_stop", "先に届いた方で売る。損切りは%.0f%%の滑りを見込む" % (slip * 100))
            s_["p_fill"] = float((th < ts_).mean()); s_["p_stop"] = float((ts_ < th).mean()); s_["limit"] = L_; s_["stop"] = S_
            strategies.append(s_)
    i10 = LIM.index(1.10) if 1.10 in LIM else 0
    hit = FU[:, i10] < 32767
    v = 0.5 * (1 - fee_t) + 0.5 * np.where(hit, LIM[i10] * (1 - fee_m), tend * (1 - fee_t))
    strategies.append(stat(v, "半分を今売り、残りは指値 +%.0f%%" % ((LIM[i10] - 1) * 100), "partial", "半分は成行、半分は指値"))
    if len(LIM) >= 4:
        a_, b_ = LIM[1], LIM[3]
        h1, h2 = FU[:, 1] < 32767, FU[:, 3] < 32767
        v = 0.5 * np.where(h1, a_, tend) + 0.5 * np.where(h2, b_, tend)
        strategies.append(stat(v * (1 - fee_m), "段階的な指値（+%.0f%%で半分、+%.0f%%で残り）" % ((a_ - 1) * 100, (b_ - 1) * 100), "ladder", "届いた分だけ売り、残りは年末まで保有"))
    strat = {"until": iso(ye)[:10], "s0_jpy": s0j, "items": strategies,
             "note": "1枚あたり・今の価格を1とした年末までの手取りの分布（モデルA・Cの道筋）。税金（雑所得）は含めない。保有枚数・取得額を掛けた損益は画面で本人の値から計算する。推奨ではなく比較の材料"}

    # ---------- 段階4：モデルE（機械学習、参考表示）と相場の状態の推定 ----------
    try:
        bdf = pd.read_csv(os.path.join(ROOT, "data/raw/prices/binance_btc_usdt_1d.csv"))
        btc = pd.Series(bdf.close.astype(float).values, index=pd.to_datetime(bdf.date))
        model_e = ME.run(close, fgs, btc, pd.Timestamp(now.date()))
    except Exception as ex:
        model_e = {"error": str(ex)}
    # チャート形状・フィボナッチ等（モデルP、参考表示・重み0。engine/chartpat.py）
    try:
        patterns = CP.analyze(ob, fx0)
    except Exception as ex:
        patterns = {"error": str(ex)}
    try:
        hmm = ME.regime_hmm(close)
    except Exception as ex:
        hmm = {"error": str(ex)}

    # 扇形のグラフ用（円・日ごとの分位）：モデルA・Cの道筋
    fan = {"days": [iso(now + timedelta(days=s + 1))[:10] for s in range(T)],
           "q": [[round(float(np.percentile(fanE[:, s], q)), 2) for q in Q] for s in range(T)]}
    ind = IND.build(ROOT, close, fed)
    model_info = {"B": {"features": [{"name": nm, "now": featB.get(c)} for c, nm in BD.FEATURES],
                        "per_h": [{"h": x["h"], "k": x["k"], "p_up_raw": x["p_up_raw"], "dates": x["dates"]} for x in infoB]},
                  "D": {"per_h": infoD, "rule": "過去の各年の同じ時期の値動き。今年と4年サイクルの位置が同じ年（%s年など）は重み2倍" % ("・".join(str(y) for y in infoD[0]["years"] if y % 4 == now.year % 4))},
                  "iv": {"iv30": iv30, "points": term["points"] if term else None, "fetched_at": term["fetched_at"] if term else None}}

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

    # ---------- 分岐のシナリオ（もし〜なら）：モデルAの道筋を、影響の大きいイベントの結果の組み合わせで分ける ----------
    def qstat(m):
        x = JA[m, yi]
        return {"share": float(m.mean()), "p_up_ye": float((x > s0j).mean()), "median_ye_jpy": float(np.median(x)),
                "q10_ye_jpy": float(np.percentile(x, 10)), "q90_ye_jpy": float(np.percentile(x, 90)),
                "p_up_1w": float((JA[m, 1] > s0j).mean()) if JA.shape[1] > 1 else None,
                "p_up_1m": float((JA[m, 2] > s0j).mean()) if JA.shape[1] > 2 else None}
    # 結果ごとの条件付き予想（全期間）
    for e_ in ev_out:
        f = FA.get(e_["id"])
        for oi, r in enumerate(e_["outcomes"]):
            m = f == oi
            if m.mean() >= 0.005:
                r.update(qstat(m))
    cand = [e_ for e_ in sorted(ev_out, key=lambda x: -x["swing_p_up_ye"] * (1 - max(r["share"] for r in x["outcomes"]))) if sum(1 for r in e_["outcomes"] if r["share"] >= 0.01) >= 2]
    tree_ev = cand[:3]
    combos = []
    if tree_ev:
        key = np.zeros(len(JA), np.int64)
        for e_ in tree_ev:
            key = key * 10 + FA[e_["id"]].astype(np.int64)
        uk, inv = np.unique(key, return_inverse=True)
        for j, kk in enumerate(uk):
            m = inv == j
            if m.mean() < 0.003:
                continue
            digits = [int(c_) for c_ in str(kk).zfill(len(tree_ev))]
            cond = [{"event": e_["name"], "outcome": e_["outcomes"][d]["label"]} for e_, d in zip(tree_ev, digits)]
            combos.append({"conditions": cond, **qstat(m)})
        combos.sort(key=lambda x: -x["share"])
    # 強気・基本・弱気：組み合わせを年末の中央値の順に並べ、下位25%・上位25%（確率の累計）で分ける
    groups = []
    if combos:
        srt = sorted(combos, key=lambda x: x["median_ye_jpy"])
        tot = sum(c_["share"] for c_ in srt); cum = 0.0
        lab = []
        for c_ in srt:
            mid = (cum + c_["share"] / 2) / tot
            lab.append("弱気" if mid < 0.25 else "強気" if mid > 0.75 else "基本")
            cum += c_["share"]
        for g in ("強気", "基本", "弱気"):
            mem = [c_ for c_, l_ in zip(srt, lab) if l_ == g]
            if not mem:
                continue
            w_ = np.array([c_["share"] for c_ in mem])
            groups.append({"name": g, "share": float(w_.sum()),
                           "median_ye_jpy": float(np.average([c_["median_ye_jpy"] for c_ in mem], weights=w_)),
                           "p_up_ye": float(np.average([c_["p_up_ye"] for c_ in mem], weights=w_)),
                           "range_ye_jpy": [float(min(c_["q10_ye_jpy"] for c_ in mem)), float(max(c_["q90_ye_jpy"] for c_ in mem))],
                           "main": [" × ".join(x["outcome"] for x in c_["conditions"]) for c_ in sorted(mem, key=lambda x: -x["share"])[:3]]})
    scenarios = {"events": [e_["name"] for e_ in tree_ev], "combos": combos[:27], "groups": groups,
                 "note": "モデルA（イベント型モンテカルロ、%s本）の道筋を、年末に上がる確率を大きく変え、結果がまだ読めない3つのイベント（影響の幅×不確かさ）の結果の組み合わせで分けたもの。"
                         "確率は各組み合わせが起きる割合、価格は年末（円）。他のイベントの結果は組み合わせの中で確率どおりに混ざっている。"
                         "強気・基本・弱気は、組み合わせを年末の中央値の順に並べ、確率の累計の下位25%%・上位25%%で分けた" % format(nA, ",")}

    elapsed = time.time() - t0
    in_hash = hashlib.sha256(json.dumps([usd["price"], jpy["price"], vol["daily"], fed and fed["p_hike"], evdoc["updated_at"], ev_notes.get("ledger_updated_at"), ENGINE_VERSION, N], default=str).encode()).hexdigest()[:12]
    rec = {
        "run_id": run_id, "at": iso(now), "mode": a.mode, "engine_version": ENGINE_VERSION, "input_hash": in_hash,
        "seed": seed, "paths": {"A": nA, "B": nB, "C": nC, "D": nD, "T": nT}, "weights": {k: W[k] / wsum for k in MS},
        "spot": {"usd": s0u, "jpy": s0j, "usd_at": iso(usd["at"]), "jpy_at": iso(jpy["at"]), "usd_source": usd["source"], "jpy_source": jpy["source"]},
        "vol": {**vol, "base_sig_daily": base_sig, "event_var_daily": ev_var, "fx_daily": fxv, "target_daily": target, "iv30": iv30,
                "basis": vol_basis},
        "event_sources": {"changes": ev_notes["changes"], "ledger_updated_at": ev_notes["ledger_updated_at"], "reaction_stats": ev_notes["stats"]},
        "touch": touch, "bottom": bottom, "strategies": strat, "model_e": model_e, "scenarios": scenarios,
        "technicals": ({k: v for k, v in tech.items() if k != "states"} | {"model_t": infoT}) if tech else None, "patterns": patterns, "regime_hmm": hmm, "indicators": ind, "model_info": model_info,
        "fed": fed, "regime": reg, "forecasts": forecasts, "events": ev_out, "events_skipped": skipped,
        "backtest_c": bt, "elapsed_sec": round(elapsed, 1),
        "assumptions": ["予想の計算はこのプログラムで行い、AIは数字を作らない",
                        "モデルA：イベント型モンテカルロ。確率は📰イベント台帳（市場・実績を優先）、米国の経済指標・FOMCの影響の大きさは過去事実データベース（発表日の値動きの実績、期待値0の形）。地政学・需給・株の局面は「判断」",
                        "モデルB：今と似た過去の日（7日・30日の騰落率、200日線からの乖離、30日の変動率、恐怖強欲指数）の、その後の値動き",
                        "モデルC：Deribitの予想変動率（満期ごと）による、方向の偏りのない分布",
                        "モデルD：過去の同じ時期の値動き（4年サイクルの位置が同じ年は重み2倍）。件数が少ない",
                        "重み A:B:C:D:T = 2:2:2:1:2（モデルT：テクニカル指標のパターン）",
                        "上がる確率は、過去検証（モデルB・C・D・T）で作った較正（engine/calibration.json）で補正した値。補正前の値も記録",
                        "モデルE（機械学習）は前進型の検証で基準を上回るまで重み0（参考表示）"],
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
                   "regime_hmm": {k: v for k, v in hmm.items() if k != "last30"} if isinstance(hmm, dict) else None,
                   "usd_at": iso(usd["at"]), "jpy_at": iso(jpy["at"]), "usd_source": usd["source"], "jpy_source": jpy["source"],
                   "fx": fx0, "fx_source": fx["source"]},
        "fallbacks": fallbacks,
        "forecast": {"items": [{k: v for k, v in f.items() if k != "models"} | {"p" + m: f["models"][m]["p_up_jpy"] for m in f["models"]} for f in forecasts],
        "touch": touch, "bottom": bottom, "iv": model_info["iv"], "strategies": strat,
                     "scenarios": {"events": scenarios["events"], "groups": scenarios["groups"], "note": scenarios["note"],
                                   "combos": [{"conditions": [{"outcome": x["outcome"]} for x in c_["conditions"]], "share": c_["share"], "p_up_ye": c_["p_up_ye"],
                                               "median_ye_jpy": c_["median_ye_jpy"]} for c_ in scenarios["combos"][:12]]},
                     "technicals": ({"date": tech.get("date"), "composite": tech.get("composite"), "values": tech.get("values"),
                                     "items": [{"name": x["name"], "state": x["state"], "signal": x["signal"], "how": x["how"],
                                                "stats": {"30": x["stats"].get("30")} if x["stats"] else {}} for x in tech.get("items", [])],
                                     "model_t": [{kk: vv for kk, vv in x.items() if kk != "dates" or i_ == 0} for i_, x in enumerate(infoT[:1])],
                                     "error": tech.get("error")}) if tech else None,
                     "model_e": {"features": model_e.get("features"), "rule": model_e.get("rule"), "error": model_e.get("error"),
                                 "per_h": {k: {kk: vv for kk, vv in v.items() if kk != "walk_forward"} for k, v in (model_e.get("per_h") or {}).items()}},
                     "patterns": patterns,
                     "model_b": {"features": model_info["B"]["features"], "dates": infoB[1]["dates"] if len(infoB) > 1 else [], "k": infoB[0]["k"]},
                     "model_d": {"years": infoD[-1]["years"], "rule": model_info["D"]["rule"]},
                     "vol_annual": vol["annual"], "vol_basis": rec["vol"]["basis"], "weights": rec["weights"], "paths": rec["paths"],
                     "seed": seed, "input_hash": in_hash, "fan": fan, "assumptions": rec["assumptions"]},
        "derivatives": der, "macro": {**mac, "fed": fed, "fedwatch": None},
        "indicators": {**ind, "items": [{k: v for k, v in x.items()} for x in ind["items"]]},
        "events": [{k: v for k, v in e.items() if k not in ("p_reason", "when_basis")} for e in ev_out], "events_skipped": skipped,
        "event_sources": {"changes": ev_notes["changes"], "ledger_updated_at": ev_notes["ledger_updated_at"]},
        "news": None,
        "verification": {"backtest_c": bt, "scorecard": sc_live if isinstance(sc_live, dict) and "error" not in sc_live else None,
                         "backtest": ({"start": btx["start"], "step_days": btx["step_days"], "origins": btx["origins"], "results": btx["results"], "calibration": btx.get("calibration")} if btx else None),
                         "platt": calib["horizons"] if calib else None,
                         "rule": "方向の信頼度：運用中の採点30件以上でブライアスコア≦0.25なら中、100件以上で≦0.24なら高。範囲の信頼度：過去検証の80%範囲の的中率75〜85%（運用中20件以上なら70〜90%も）で中",
                         "note": "運用中の予想は毎回の実行で、期限が来た分を採点（Binance 1時間足）。過去検証は engine/backtest.py（2020年〜、7日おき）"},
        "fetchers": fst["fetchers"], "freshness": [{k: v for k, v in x.items() if k != "age_min"} for x in fst["freshness"]], "state_updated_at": fst["state_updated_at"],
        "prices_jpy": prices,
        "record_file": os.path.relpath(fn, ROOT), "elapsed_sec": round(elapsed, 1),
    }
    try:
        al = AL.check(ROOT, rec, upd, cfg, fst)
    except Exception as ex:
        al = {"items": [], "error": str(ex)}
    upd["alerts"] = al
    rec["alerts"] = al
    json.dump(rec, open(fn, "w"), ensure_ascii=False, indent=1)
    json.dump(rec, open(os.path.join(ROOT, "forecasts", "latest.json"), "w"), ensure_ascii=False, indent=1)
    upd = _round(upd)
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    s = json.dumps(upd, ensure_ascii=False, separators=(",", ":"), default=str)
    open(os.path.join(HERE, "out", "update.json"), "w").write(s)

    # ---------- 要点 ----------
    L = (["【通知】" + " ／ ".join(x["text"] for x in al["items"] if x["level"] == "重要")] if any(x["level"] == "重要" for x in al["items"]) else []) + ["XRP予想（%s・%s）" % (iso(now), a.mode),
         "現在値：%.2f円（%s）／$%.4f（%s）、相場の状態：%s" % (s0j, jpy["source"], s0u, usd["source"], reg["state"])]
    for f in forecasts:
        L.append("%s：上がる確率 約%.0f%%（%.0f〜%.0f%%）、50%%範囲 %.1f〜%.1f円、80%%範囲 %.1f〜%.1f円、信頼度 方向%s・範囲%s" % (
            f["label"], f["p_up_jpy"] * 100, f["p_band"][0] * 100, f["p_band"][1] * 100,
            f["range50_jpy"][0], f["range50_jpy"][1], f["range80_jpy"][0], f["range80_jpy"][1], f["confidence"], f["confidence_range"]))
    if fed:
        L.append("12月FOMCの利上げ確率（予測市場）：%.0f%%" % (fed["p_hike"] * 100))
    if al["items"]:
        L.append("通知の条件に当たったもの：" + " ／ ".join(x["text"] for x in al["items"]))
    if isinstance(hmm, dict) and hmm.get("now"):
        L.append("相場の状態：ルール「%s」・統計の推定「%s」（%.0f%%）" % (reg["state"], hmm["now"], hmm["p_now"] * 100))
    if fallbacks:
        L.append("代わりの取得先：" + "、".join(x["item"] + "→" + x["used"] for x in fallbacks))
    L.append("変動率（年率）：実現 %.0f%%・予想変動率（30日）%s、計算 %.1f秒、記録 %s、ダッシュボード用 %d KB" % (vol["annual"] * 100, ("%.0f%%" % (iv30 * 100)) if iv30 else "取れず", elapsed, os.path.relpath(fn, ROOT), len(s.encode()) // 1024))
    open(os.path.join(HERE, "out", "summary.txt"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
