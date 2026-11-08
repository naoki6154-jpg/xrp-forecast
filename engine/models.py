"""モデルA（イベント型モンテカルロ）とモデルC（変動率・オプション代用）。
どちらも同じ形の答え（期限ごとの価格の標本）を返す。乱数は seed から作るので、同じ入力なら毎回同じ結果になる。
"""
import math
from datetime import datetime, timedelta
import numpy as np

DAY = timedelta(days=1)


def _std_t(rng, df, shape):
    z = rng.standard_t(df, shape)
    return (z / math.sqrt(df / (df - 2))).astype(np.float32)


def step_vol_mult(now_jst, T, weekend_mult):
    """各ステップ（1日）の変動の倍率。終わりの日が土日なら小さくする。平均が1になるよう正規化。"""
    m = np.array([weekend_mult if (now_jst + (s + 1) * DAY).weekday() >= 5 else 1.0 for s in range(T)], np.float64)
    return (m / math.sqrt((m ** 2).mean())).astype(np.float32)


def compile_events(events, now_jst, T, fed):
    """イベント登録表を、ステップ番号つきの形に直す。過ぎたイベントと期間外は外す。"""
    out, skipped = [], []
    for ev in events:
        tm = ev["timing"]
        e = dict(ev)
        if tm.get("from_start"):
            e["step"] = ("fixed", 0)
        elif "at" in tm:
            at = datetime.fromisoformat(tm["at"])
            dt = (at - now_jst).total_seconds() / 86400
            if dt < 0:
                skipped.append({"id": ev["id"], "reason": "発表済み（結果を登録して確定させる）"}); continue
            s = int(math.floor(dt))
            if s >= T:
                skipped.append({"id": ev["id"], "reason": "予想期間の外"}); continue
            e["step"] = ("fixed", s)
        elif "window" in tm:
            a = datetime.fromisoformat(tm["window"][0] + "T00:00:00+09:00")
            b = datetime.fromisoformat(tm["window"][1] + "T23:59:00+09:00")
            sa = max(0, int(math.floor((a - now_jst).total_seconds() / 86400)))
            sb = min(T - 1, int(math.floor((b - now_jst).total_seconds() / 86400)))
            if sb < sa:
                skipped.append({"id": ev["id"], "reason": "期間が過ぎた"}); continue
            e["step"] = ("window", sa, sb)
        probs = [o["p"] for o in ev["outcomes"]]
        e["p_used_basis"] = ev.get("p_basis")
        if ev.get("p_from_market") == "polymarket_fed_year_end":
            if fed and fed.get("p_hike") is not None:
                ph = fed["p_hike"]
                probs = [1 - ph, ph]
                e["p_used_basis"] = "市場"
            else:
                probs = ev.get("p_fallback", probs)
                e["p_used_basis"] = "判断"
        if e.get("fixed_outcome"):
            labels = [o["label"] for o in ev["outcomes"]]
            if e["fixed_outcome"] in labels:
                probs = [1.0 if l == e["fixed_outcome"] else 0.0 for l in labels]
                e["p_used_basis"] = "結果確定"
        s = sum(probs)
        e["probs"] = [p / s for p in probs]
        if e.get("neutral_jump"):
            import events_sync
            events_sync.renormalize_neutral(e, e["probs"])
        out.append(e)
    return out, skipped


def _apply_effects(mu, volm, fxmu, idx, e_step, effects, rng, T):
    """idx：この結果になった道筋の番号。e_step：その道筋でのイベントのステップ（配列）。"""
    if idx.size == 0:
        return
    for ef in effects:
        k = ef["kind"]
        if k in ("jump", "fx"):
            s = e_step + ef.get("day", 0)
            ok = s < T
            ii, ss = idx[ok], s[ok]
            amt = np.full(ii.size, ef["amt"], np.float32)
            if ef.get("noise"):
                amt += rng.normal(0, ef["noise"], ii.size).astype(np.float32)
            if k == "jump":
                np.add.at(mu, (ii, ss), amt)
            else:
                np.add.at(fxmu, (ii, ss), amt)
        elif k in ("drift", "vol"):
            a, b = ef["from"], ef["to"]
            per = ef.get("total", 0.0) / (b - a + 1)
            for off in range(a, b + 1):
                s = e_step + off
                ok = s < T
                ii, ss = idx[ok], s[ok]
                if k == "drift":
                    np.add.at(mu, (ii, ss), np.float32(per))
                else:
                    volm[ii, ss] *= np.float32(ef["mult"])


def model_a_chunk(seed, n, T, base_sig, vmult, events_c, jumps, fx_sig, t_df, h_steps, include_noise=True):
    rng = np.random.default_rng(seed)
    mu = np.zeros((n, T), np.float32)
    volm = np.ones((n, T), np.float32)
    fxmu = np.zeros((n, T), np.float32)
    drawn = {}
    for ev in events_c:
        probs = np.array(ev["probs"], np.float64)
        labels = [o["label"] for o in ev["outcomes"]]
        if ev.get("adjust") and len(probs) == 2:
            tgt = labels.index(ev["adjust"][0]["target"])
            p = np.full(n, probs[tgt])
            for ad in ev["adjust"]:
                if ad["if"] in drawn:
                    src_ev, src_draw = drawn[ad["if"]]
                    oi = [o["label"] for o in src_ev["outcomes"]].index(ad["outcome"])
                    p += np.where(src_draw == oi, ad["delta"], 0.0)
            p = np.clip(p, 0.03, 0.97)
            u = rng.random(n)
            draw = np.where(u < p, tgt, 1 - tgt).astype(np.int8)
        else:
            draw = rng.choice(len(probs), n, p=probs).astype(np.int8)
        drawn[ev["id"]] = (ev, draw)
        st = ev["step"]
        if st[0] == "fixed":
            e_step = np.full(n, st[1], np.int64)
        else:
            e_step = rng.integers(st[1], st[2] + 1, n).astype(np.int64)
        for oi, o in enumerate(ev["outcomes"]):
            idx = np.where(draw == oi)[0]
            _apply_effects(mu, volm, fxmu, idx, e_step[idx], o["effects"], rng, T)
    # 銘柄固有の不定期な急騰・急落（ポアソン）
    mu += (rng.random((n, T)) < jumps["pos_rate_per_day"]) * np.float32(jumps["pos_size"])
    mu += (rng.random((n, T)) < jumps["neg_rate_per_day"]) * np.float32(jumps["neg_size"])
    if include_noise:
        s = base_sig * vmult[None, :] * volm
        r = mu - 0.5 * s * s + s * _std_t(rng, t_df, (n, T))
        fxr = fxmu + np.float32(fx_sig) * rng.standard_normal((n, T)).astype(np.float32)
    else:
        r, fxr = mu, fxmu
    lx = np.cumsum(r, axis=1)
    lfx = np.cumsum(fxr, axis=1)
    factors = {k: v[1] for k, v in drawn.items()}
    return lx, lfx, factors


def model_c_chunk(seed, n, T, sig, fx_sig, t_df):
    """方向の偏りなし（期待値ゼロ）の分布。sig は1日ごとの変動（Deribitの予想変動率の期間構造。無ければ実現変動率の一定値）。"""
    rng = np.random.default_rng(seed)
    s = np.asarray(sig, dtype=np.float32)
    if s.ndim == 1:
        s = s[None, :]
    r = -0.5 * s * s + s * _std_t(rng, t_df, (n, T))
    lx = np.cumsum(r, axis=1)
    lfx = np.cumsum(np.float32(fx_sig) * rng.standard_normal((n, T)).astype(np.float32), axis=1)
    return lx, lfx


def calibrate_base_sig(seed, T, target, vmult, events_c, jumps, floor_ratio):
    """イベントと急騰・急落の分の変動を差し引いて、毎日の基本の変動を決める（合計が目標の変動率になるように）。"""
    lx, _, _ = model_a_chunk(seed, 40000, T, 0.0, vmult, events_c, jumps, 0.0, 4, None, include_noise=False)
    ev_var_day = float(np.var(lx[:, -1])) / T
    base2 = max(target ** 2 - ev_var_day, (floor_ratio * target) ** 2)
    return math.sqrt(base2), ev_var_day
