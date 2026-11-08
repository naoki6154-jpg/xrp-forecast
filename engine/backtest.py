"""段階3：過去検証（設計書第9章）。2018年からの日足で、その日までの情報だけを使って予想し、実際の結果で採点する。
対象はモデルB・C・D（モデルAはイベントの確率が「判断」で過去に再現できないため対象外）。
出力：reports/backtest.json と reports/backtest.md   使い方：python3 engine/backtest.py [--step 7] [--start 2020-01-01]
"""
import argparse, json, math, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import inputs as I, models_bd as BD

HS = {"1d": 1, "1w": 7, "1m": 30}
QS = (10, 25, 50, 75, 90)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--step", type=int, default=7); ap.add_argument("--start", default="2020-01-01"); ap.add_argument("--n", type=int, default=4000)
    a = ap.parse_args()
    close = I.load_daily_usd(ROOT)
    fgdf = pd.read_csv(os.path.join(ROOT, "data/raw/sentiment/fear_greed.csv"))
    fgs = pd.Series(fgdf.value.astype(float).values, index=pd.to_datetime(fgdf.date))
    full = BD.daily_frame(close, fgs)
    lr = np.log(close).diff()
    ew = lr.ewm(alpha=0.06, adjust=False).std().shift(0)  # λ=0.94
    rv90 = lr.rolling(90).std()
    sig_all = 0.5 * ew + 0.5 * rv90
    rows = []
    origins = [d for d in full.index if d >= pd.Timestamp(a.start) and (d - pd.Timestamp(a.start)).days % a.step == 0]
    steps = list(HS.values())
    for k, t in enumerate(origins):
        pos = full.index.get_loc(t)
        if pos + max(steps) >= len(full):
            break
        sub = full.iloc[:pos + 1]
        sig = float(sig_all.iloc[pos])
        if not np.isfinite(sig):
            continue
        n = a.n; seed = 1000 + k
        rB, _, _ = BD.model_b(sub, steps, n, seed, sig)
        rD, _ = BD.model_d(sub, t, steps, n, seed + 1, sig)
        rng = np.random.default_rng(seed + 2)
        rC = (rng.standard_t(4, (n, len(steps))) / math.sqrt(2)) * sig * np.sqrt(np.array(steps))[None, :]
        rE = np.concatenate([rB[: n * 2 // 5], rC[: n * 2 // 5], rD[: n // 5]]) if False else np.concatenate([rB[:2 * n // 5], rC[:2 * n // 5], rD[:n // 5]])
        for j, (hn, h) in enumerate(HS.items()):
            actual = float(np.log(full.close.iloc[pos + h] / full.close.iloc[pos]))
            for mn, r in (("B", rB), ("C", rC), ("D", rD), ("E", rE)):
                x = r[:, j]
                qv = np.percentile(x, QS)
                rows.append(dict(t=str(t.date()), h=hn, m=mn, p_up=float((x > 0).mean()), up=int(actual > 0), actual=actual,
                                 in50=int(qv[1] <= actual <= qv[3]), in80=int(qv[0] <= actual <= qv[4]),
                                 pin=float(np.mean([max(q / 100 * (actual - v), (q / 100 - 1) * (actual - v)) for q, v in zip(QS, qv)]))))
    df = pd.DataFrame(rows)
    out = {"start": a.start, "step_days": a.step, "origins": len(origins), "n_paths": a.n, "results": {}, "calibration": {}}
    for (h, m), g in df.groupby(["h", "m"]):
        brier = float(((g.p_up - g.up) ** 2).mean()); base = float(g.up.mean())
        out["results"].setdefault(h, {})[m] = dict(n=len(g), brier=brier, brier_base=float(((base - g.up) ** 2).mean()),
                                                 in50=float(g.in50.mean()), in80=float(g.in80.mean()), pinball=float(g.pin.mean()), up_rate=base)
        if m == "E":
            bins = pd.cut(g.p_up, [0, .35, .45, .55, .65, 1.0])
            out["calibration"][h] = [dict(bin=str(b), n=int(len(x)), pred=float(x.p_up.mean()), actual=float(x.up.mean())) for b, x in g.groupby(bins, observed=True)]
    # 確率の較正（設計書 第6章）：混合Eの上がる確率 p を、ロジスティック回帰 p' = σ(a + b·logit(p)) で実際の頻度に合わせる
    from sklearn.linear_model import LogisticRegression
    cal = {}
    for h in HS:
        g = df[(df.h == h) & (df.m == "E")]
        x = np.log(np.clip(g.p_up, 0.01, 0.99) / (1 - np.clip(g.p_up, 0.01, 0.99))).values.reshape(-1, 1)
        lr_ = LogisticRegression(C=1.0).fit(x, g.up.values)
        cal[h] = {"a": float(lr_.intercept_[0]), "b": float(lr_.coef_[0][0]), "n": int(len(g)), "up_rate": float(g.up.mean())}
    out["platt"] = cal
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    json.dump({"made_at": pd.Timestamp.now(tz="Asia/Tokyo").isoformat(timespec="minutes"), "method": "ロジスティック較正（過去検証の混合E：モデルB・C・D）", "horizons": cal,
               "note": "b が0に近いほど、上がる確率に過去の実力がない（確率を平均に寄せる）。年末は1ヶ月の値を使う"},
              open(os.path.join(ROOT, "engine/calibration.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(out, open(os.path.join(ROOT, "reports/backtest.json"), "w"), ensure_ascii=False, indent=1)
    L = ["# 過去検証（モデルB・C・D・混合E）", "", "期間：%s〜、%d日おきの起点 %d回。その日までの情報だけで予想し、実際の結果で採点。モデルAは対象外。" % (a.start, a.step, len(origins)), "",
         "| 期間 | モデル | 的中率50%範囲 | 80%範囲 | 上昇確率の誤差(Brier、小さいほど良い) | 常に同じ確率にした場合 |", "|---|---|---|---|---|---|"]
    for h in HS:
        for m in "BCDE":
            r = out["results"][h][m]
            L.append("| %s | %s | %.0f%% | %.0f%% | %.3f | %.3f |" % (h, m, r["in50"] * 100, r["in80"] * 100, r["brier"], r["brier_base"]))
    L += ["", "## 確率の較正（engine/calibration.json）", ""] + ["- %s：a=%.3f、b=%.3f（%d件、実際に上がった割合 %.0f%%）" % (h, c["a"], c["b"], c["n"], c["up_rate"] * 100) for h, c in cal.items()]
    L += ["", "50%範囲は50%、80%範囲は80%に近いほど、ばらつきの見積もりが正しい。", "", "## 混合Eの較正（予想した上がる確率と、実際に上がった割合）"]
    for h, rows_ in out["calibration"].items():
        L.append("- %s：" % h + "、".join("予想%.0f%%→実際%.0f%%(%d件)" % (r["pred"] * 100, r["actual"] * 100, r["n"]) for r in rows_))
    open(os.path.join(ROOT, "reports/backtest.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))

if __name__ == "__main__":
    main()
