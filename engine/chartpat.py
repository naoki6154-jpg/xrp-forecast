"""チャート形状・フィボナッチ・価格帯などの手法（モデルP）。2026-11-09 追加。参考表示（重み0）。

どの手法も、「その日までに分かっていること」だけで判定する（未来を見ない）。
  高値・安値の拾い方：ジグザグ法。直近の高値（安値）から一定の割合（値幅のおよそ3倍・最低10%）戻ったら、その高値（安値）を「確定」とする。
手法（10種）：
  fib       フィボナッチ（直近の上げ下げ幅に対する戻りの位置：0〜23.6〜38.2〜50〜61.8〜78.6〜100%）
  dbl       ダブルトップ／ダブルボトム（高値・安値が2回そろい、間の谷・山を抜けたか）
  hs        三尊（ヘッドアンドショルダー）／逆三尊
  tri       持ち合い・チャネル（上下の線の傾きから：上昇／下降チャネル、上昇／下降／対称三角、レンジ。位置と抜け）
  cup       カップウィズハンドル（丸い底→右の縁が左の縁に近づく→浅い押し→縁を抜けたか）
  sr        サポート・レジスタンス（過去1年の高値・安値の集まり。近さと抜け）
  pivotw    週足のピボットポイント（前週の高値・安値・終値から）
  donchian  ドンチアン・ブレイク（20日・55日の高値／安値を抜けたか。タートル）
  dow       ダウ理論（高値・安値の切り上げ／切り下げ）
  vprofile  価格帯別出来高（直近90日の出来高が最も多い価格＝POC と、70%の価格帯）
採点：各手法の「今日の状態」ごとに、同じ状態だった過去の日のその後（1日・7日・30日）の上昇の割合を数え、
  前進型（その時点までに結果が分かっている日だけを使う）でブライアスコアを基準（いつも同じ確率）と比べる。
  合格の条件はモデルEと同じ（基準より0.002以上良く、8割以上の年で上回る）。合格するまで予想の重みは0。
エリオット波動・ギャン・ワイコフは、人によって数え方が違い、プログラムで一意に判定できないため対象外。
"""
import math
import numpy as np
import pandas as pd

HS = {"1d": 1, "1w": 7, "1m": 30}
K_SHRINK = 20
NAMES = {"fib": "フィボナッチ", "dbl": "ダブルトップ／ボトム", "hs": "三尊／逆三尊", "tri": "持ち合い・チャネル", "cup": "カップウィズハンドル",
         "sr": "サポート・レジスタンス", "pivotw": "週足ピボット", "donchian": "ドンチアン・ブレイク", "dow": "ダウ理論", "vprofile": "価格帯別出来高"}
FIB = [0.236, 0.382, 0.5, 0.618, 0.786]
LABEL_JA = {
    "none": "該当なし", "new_high": "直近の高値を更新中", "broke_low": "安値を割り込み",
    "dtop_forming": "ダブルトップ（形成中）", "dtop_break": "ダブルトップ（谷を割った）", "dbot_forming": "ダブルボトム（形成中）", "dbot_break": "ダブルボトム（山を抜けた）",
    "hs_forming": "三尊（形成中）", "hs_break": "三尊（ネックラインを割った）", "ihs_forming": "逆三尊（形成中）", "ihs_break": "逆三尊（ネックラインを抜けた）",
    "cup_handle": "カップ（ハンドル形成中）", "cup_break": "カップウィズハンドル（縁を抜けた）",
    "near_sup": "サポート付近", "near_res": "レジスタンス付近", "mid": "価格帯の中間", "brk_up": "レジスタンスを上抜け", "brk_dn": "サポートを下抜け",
    "brk55_up": "55日高値を更新", "brk20_up": "20日高値を更新", "brk20_dn": "20日安値を更新", "brk55_dn": "55日安値を更新",
    "up": "切り上げ（上昇トレンド）", "down": "切り下げ（下降トレンド）", "mixed": "どちらでもない",
    "above_va": "出来高の山の上", "va_above_poc": "山の中・POCより上", "va_below_poc": "山の中・POCより下", "below_va": "出来高の山の下",
    "gt_r1": "R1より上", "p_r1": "P〜R1", "s1_p": "S1〜P", "lt_s1": "S1より下",
}


def _ja(label):
    if label in LABEL_JA:
        return LABEL_JA[label]
    p = label.split("|")
    if p[0] in ("fu", "fd") and len(p) == 2:
        return ("上げの後の戻り：" if p[0] == "fu" else "下げの後の戻り：") + {"z0": "0〜23.6%", "z1": "23.6〜38.2%", "z2": "38.2〜50%", "z3": "50〜61.8%", "z4": "61.8〜78.6%", "z5": "78.6〜100%",
                                                                              "out": "全戻し（起点を超えた）", "beyond": "高値（安値）を更新中"}.get(p[1], p[1])
    if p[0] == "tri":
        shape = {"asc": "上昇三角", "desc": "下降三角", "sym": "対称三角", "chup": "上昇チャネル", "chdn": "下降チャネル", "range": "レンジ"}.get(p[1], p[1])
        pos = {"brk_up": "上に抜け", "brk_dn": "下に抜け", "upper": "上限付近", "lower": "下限付近", "mid": "中ほど"}.get(p[2], p[2]) if len(p) > 2 else ""
        return shape + "・" + pos
    return label


# ---------- 高値・安値（ジグザグ） ----------
def zigzag(h, l, c):
    n = len(c)
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])]) / c[1:]
    atr = pd.Series(np.r_[tr[0], tr]).rolling(14, min_periods=3).mean().bfill().values
    thr = np.maximum(0.10, 3.0 * atr)
    piv, cnt = [], np.zeros(n, int)
    hi, hi_i, lo, lo_i, trend = h[0], 0, l[0], 0, 0
    for t in range(1, n):
        th = thr[t]
        if trend == 0:
            if h[t] > hi:
                hi, hi_i = h[t], t
            if l[t] < lo:
                lo, lo_i = l[t], t
            if h[t] >= lo * (1 + th) and lo_i < t:
                piv.append((lo_i, lo, "L", t)); trend = 1; hi, hi_i = h[t], t
            elif l[t] <= hi * (1 - th) and hi_i < t:
                piv.append((hi_i, hi, "H", t)); trend = -1; lo, lo_i = l[t], t
        elif trend == 1:
            if h[t] > hi:
                hi, hi_i = h[t], t
            elif l[t] <= hi * (1 - th):
                piv.append((hi_i, hi, "H", t)); trend = -1; lo, lo_i = l[t], t
        else:
            if l[t] < lo:
                lo, lo_i = l[t], t
            elif h[t] >= lo * (1 + th):
                piv.append((lo_i, lo, "L", t)); trend = 1; hi, hi_i = h[t], t
        cnt[t] = len(piv)
    return piv, cnt, thr, (trend, hi, hi_i, lo, lo_i)


# ---------- 各手法の判定（t 日時点で分かっていることだけ） ----------
def m_fib(P, c, t):
    if len(P) < 2:
        return "none", {}
    (ia, a, ka, _), (ib, b, kb, _) = P[-2], P[-1]
    if kb == "H":
        rng = b - a
        if rng <= 0:
            return "none", {}
        r = (b - c) / rng
        pre = "fu"
    else:
        rng = a - b
        if rng <= 0:
            return "none", {}
        r = (c - b) / rng
        pre = "fd"
    z = "beyond" if r < 0 else "out" if r > 1 else "z%d" % int(np.searchsorted(FIB, r, side="right"))
    return "%s|%s" % (pre, z), {"a": a, "b": b, "kb": kb, "r": r}


def m_dbl(P, c, t):
    if len(P) < 3:
        return "none", {}
    (i0, p0, k0, _), (i1, p1, k1, _), (i2, p2, k2, _) = P[-3:]
    if t - i0 > 150:
        return "none", {}
    tol = 0.05
    if (k0, k1, k2) == ("H", "L", "H") and abs(p0 - p2) / max(p0, p2) < tol and (min(p0, p2) - p1) / min(p0, p2) > 0.08:
        if c < p1 and t - i2 <= 45:
            return "dtop_break", {"neck": p1, "top": (p0 + p2) / 2, "target": p1 - ((p0 + p2) / 2 - p1)}
        if c >= p1:
            return "dtop_forming", {"neck": p1, "top": (p0 + p2) / 2, "target": p1 - ((p0 + p2) / 2 - p1)}
    if (k0, k1, k2) == ("L", "H", "L") and abs(p0 - p2) / max(p0, p2) < tol and (p1 - max(p0, p2)) / p1 > 0.08:
        if c > p1 and t - i2 <= 45:
            return "dbot_break", {"neck": p1, "bottom": (p0 + p2) / 2, "target": p1 + (p1 - (p0 + p2) / 2)}
        if c <= p1:
            return "dbot_forming", {"neck": p1, "bottom": (p0 + p2) / 2, "target": p1 + (p1 - (p0 + p2) / 2)}
    return "none", {}


def m_hs(P, c, t):
    if len(P) < 5:
        return "none", {}
    S = P[-5:]
    ks = "".join(s[2] for s in S)
    pr = [s[1] for s in S]
    if t - S[0][0] > 250:
        return "none", {}
    if ks == "HLHLH":
        h1, l1, h2, l2, h3 = pr
        if h2 > max(h1, h3) * 1.02 and abs(h1 - h3) / h2 < 0.15 and abs(l1 - l2) / max(l1, l2) < 0.12:
            neck = (l1 + l2) / 2
            tg = neck - (h2 - neck)
            if c < neck * 0.99 and t - S[-1][0] <= 60:
                return "hs_break", {"neck": neck, "head": h2, "target": tg}
            if c >= neck:
                return "hs_forming", {"neck": neck, "head": h2, "target": tg}
    if ks == "LHLHL":
        l1, h1, l2, h2, l3 = pr
        if l2 < min(l1, l3) * 0.98 and abs(l1 - l3) / max(l1, l3) < 0.15 and abs(h1 - h2) / max(h1, h2) < 0.12:
            neck = (h1 + h2) / 2
            tg = neck + (neck - l2)
            if c > neck * 1.01 and t - S[-1][0] <= 60:
                return "ihs_break", {"neck": neck, "head": l2, "target": tg}
            if c <= neck:
                return "ihs_forming", {"neck": neck, "head": l2, "target": tg}
    return "none", {}


def m_tri(P, c, t):
    H = [(s[0], s[1]) for s in P if s[2] == "H" and t - s[0] <= 220][-3:]
    L = [(s[0], s[1]) for s in P if s[2] == "L" and t - s[0] <= 220][-3:]
    if len(H) < 3 or len(L) < 3:
        return "none", {}
    xh, yh = np.array([x for x, _ in H], float), np.array([y for _, y in H])
    xl, yl = np.array([x for x, _ in L], float), np.array([y for _, y in L])
    bh, ah = np.polyfit(xh, yh, 1)
    bl, al = np.polyfit(xl, yl, 1)
    m = (yh.mean() + yl.mean()) / 2
    sh, sl = bh * 30 / m, bl * 30 / m
    uh, ll = bh * t + ah, bl * t + al
    if not (uh > ll * 1.02):
        return "none", {}
    s0 = 0.03
    fh, fl = abs(sh) < s0, abs(sl) < s0
    if fh and sl > s0:
        sh_ = "asc"
    elif fl and sh < -s0:
        sh_ = "desc"
    elif sh < -s0 and sl > s0:
        sh_ = "sym"
    elif sh > s0 and sl > s0 and abs(sh - sl) < 2.5 * s0:
        sh_ = "chup"
    elif sh < -s0 and sl < -s0 and abs(sh - sl) < 2.5 * s0:
        sh_ = "chdn"
    elif fh and fl:
        sh_ = "range"
    else:
        return "none", {}
    pos = (c - ll) / (uh - ll)
    ps = "brk_up" if c > uh * 1.01 else "brk_dn" if c < ll * 0.99 else "upper" if pos > 0.8 else "lower" if pos < 0.2 else "mid"
    return "tri|%s|%s" % (sh_, ps), {"upper": uh, "lower": ll, "pos": pos, "slope_h": sh, "slope_l": sl}


def m_cup(P, c, t):
    if len(P) < 3:
        return "none", {}
    for k in (4, 3):
        if len(P) < k:
            continue
        S = P[-k:]
        ks = "".join(s[2] for s in S)
        if ks not in ("HLHL", "HLH"):
            continue
        (il, hl, _, _), (ib, lb, _, _), (ir, hr, _, _) = S[:3]
        hand = S[3][1] if k == 4 else None
        dur = ir - il
        depth = (hl - lb) / hl
        if not (25 <= dur <= 220 and 0.12 <= depth <= 0.5 and abs(hr - hl) / hl < 0.06 and 0.3 <= (ib - il) / dur <= 0.7 and t - ir <= 60):
            continue
        low_h = min(c, hand) if hand is not None else c
        hd = (hr - low_h) / hr
        rim = max(hl, hr)
        if hd > 0.5 * depth or low_h < lb + 0.5 * (hr - lb):
            continue
        tg = rim + (rim - lb)
        if c > rim * 1.0:
            return "cup_break", {"rim": rim, "bottom": lb, "target": tg}
        return "cup_handle", {"rim": rim, "bottom": lb, "target": tg}
    return "none", {}


def m_sr(P, c, t, c5):
    prices = sorted([s[1] for s in P if t - s[0] <= 365])
    if len(prices) < 3:
        return "none", {}
    cl, cur = [], [prices[0]]
    for p in prices[1:]:
        if p <= np.mean(cur) * 1.03:
            cur.append(p)
        else:
            cl.append(cur); cur = [p]
    cl.append(cur)
    lv = [(float(np.mean(x)), len(x)) for x in cl if len(x) >= 2]
    if not lv:
        return "none", {}
    sup = [v for v, _ in lv if v <= c]
    res = [v for v, _ in lv if v > c]
    s_, r_ = (max(sup) if sup else None), (min(res) if res else None)
    info = {"support": s_, "resistance": r_}
    for v, _ in lv:
        if c5 < v * 0.995 and c > v * 1.01:
            return "brk_up", info
        if c5 > v * 1.005 and c < v * 0.99:
            return "brk_dn", info
    if s_ is not None and (c - s_) / c < 0.03:
        return "near_sup", info
    if r_ is not None and (r_ - c) / c < 0.03:
        return "near_res", info
    return "mid", info


def m_donchian(h, l, c, t):
    if t < 56:
        return "none", {}
    h55, l55 = h[t - 55:t].max(), l[t - 55:t].min()
    h20, l20 = h[t - 20:t].max(), l[t - 20:t].min()
    lab = "brk55_up" if c[t] > h55 else "brk20_up" if c[t] > h20 else "brk55_dn" if c[t] < l55 else "brk20_dn" if c[t] < l20 else "none"
    return lab, {"high20": h20, "low20": l20, "high55": h55, "low55": l55}


def m_dow(P, c, t):
    H = [s[1] for s in P if s[2] == "H"][-2:]
    L = [s[1] for s in P if s[2] == "L"][-2:]
    if len(H) < 2 or len(L) < 2:
        return "none", {}
    if H[1] > H[0] and L[1] > L[0]:
        return "up", {}
    if H[1] < H[0] and L[1] < L[0]:
        return "down", {}
    return "mixed", {}


def m_vprofile(h, l, c, v, t):
    if t < 90:
        return "none", {}
    hh, ll, cc, vv = h[t - 90:t], l[t - 90:t], c[t - 90:t], v[t - 90:t]
    lo, hi = ll.min(), hh.max()
    if hi <= lo:
        return "none", {}
    nb = 40
    edges = np.linspace(lo, hi, nb + 1)
    tp = (hh + ll + cc) / 3
    idx = np.clip(np.searchsorted(edges, tp, side="right") - 1, 0, nb - 1)
    vol = np.bincount(idx, weights=vv, minlength=nb)
    poc = int(vol.argmax())
    a = b = poc
    tot, acc = vol.sum(), vol[poc]
    while acc < 0.7 * tot and (a > 0 or b < nb - 1):
        up = vol[b + 1] if b < nb - 1 else -1
        dn = vol[a - 1] if a > 0 else -1
        if up >= dn:
            b += 1; acc += vol[b]
        else:
            a -= 1; acc += vol[a]
    val, vah, pocp = edges[a], edges[b + 1], (edges[poc] + edges[poc + 1]) / 2
    x = c[t]
    lab = "above_va" if x > vah else "below_va" if x < val else "va_above_poc" if x >= pocp else "va_below_poc"
    return lab, {"poc": pocp, "va_low": val, "va_high": vah}


def weekly_pivot(wp, wk_t, x):
    prev = wp.get(wk_t - 1)
    if prev is None:
        return "none", {}
    H, L, C = prev
    p = (H + L + C) / 3
    r1, s1 = 2 * p - L, 2 * p - H
    lab = "gt_r1" if x > r1 else "p_r1" if x >= p else "s1_p" if x >= s1 else "lt_s1"
    return lab, {"p": p, "r1": r1, "s1": s1, "r2": p + (H - L), "s2": p - (H - L)}


def compute_labels(df):
    h, l, c, v = (df[k].values for k in ("high", "low", "close", "volume"))
    n = len(c)
    piv, cnt, thr, cur = zigzag(h, l, c)
    wk = df.index.to_period("W-SUN")
    g = df.groupby(wk)
    wp = {k: (v.high.max(), v.low.min(), v.close.iloc[-1]) for k, v in g if len(v) >= 3}
    L = {m: [] for m in NAMES}
    info_last = {}
    start = 120
    for t in range(n):
        P = piv[:cnt[t]]
        res = {}
        if t >= start:
            res["fib"] = m_fib(P, c[t], t)
            res["dbl"] = m_dbl(P, c[t], t)
            res["hs"] = m_hs(P, c[t], t)
            res["tri"] = m_tri(P, c[t], t)
            res["cup"] = m_cup(P, c[t], t)
            res["sr"] = m_sr(P, c[t], t, c[t - 5])
            res["donchian"] = m_donchian(h, l, c, t)
            res["dow"] = m_dow(P, c[t], t)
            res["vprofile"] = m_vprofile(h, l, c, v, t)
            res["pivotw"] = weekly_pivot(wp, wk[t], c[t])
        for m in NAMES:
            L[m].append(res[m][0] if m in res else "none")
        if t == n - 1:
            info_last = {m: res[m][1] for m in res}
    return pd.DataFrame(L, index=df.index), info_last, piv, cur, thr


# ---------- 前進型の採点 ----------
def evaluate(labels, close, eval_start="2021-01-01"):
    lc = np.log(close.values)
    n = len(lc)
    out = {}
    for hid, hh in HS.items():
        y = np.full(n, np.nan)
        y[:n - hh] = (lc[hh:] - lc[:n - hh] > 0).astype(float)
        fr = np.full(n, np.nan)
        fr[:n - hh] = lc[hh:] - lc[:n - hh]
        per, comp = {}, {"p": np.full(n, np.nan), "base": np.full(n, np.nan)}
        for m in NAMES:
            lab = labels[m].values
            cnt, up = {}, {}
            sup = sc = 0.0
            ps = np.full(n, np.nan); bs = np.full(n, np.nan)
            for t in range(n):
                s = t - hh
                if s >= 0 and not np.isnan(y[s]):
                    ls = lab[s]
                    cnt[ls] = cnt.get(ls, 0) + 1; up[ls] = up.get(ls, 0) + y[s]
                    sup += y[s]; sc += 1
                if sc >= 100:
                    base = sup / sc
                    k = cnt.get(lab[t], 0)
                    ps[t] = (up.get(lab[t], 0) + K_SHRINK * base) / (k + K_SHRINK)
                    bs[t] = base
            per[m] = (ps, bs)
        out[hid] = {"per": per, "y": y, "fr": fr}
    return out


def summarize(labels, close, ev, now_label):
    n = len(close)
    idx = close.index
    res = {}
    for hid, hh in HS.items():
        y, fr, per = ev[hid]["y"], ev[hid]["fr"], ev[hid]["per"]
        mask_eval = (idx >= pd.Timestamp("2021-01-01"))
        stacks = []
        for m in NAMES:
            ps, bs = per[m]
            ok = mask_eval & ~np.isnan(ps) & ~np.isnan(y)
            yrs = idx.year
            wins = tot = 0
            for yr in sorted(set(yrs[ok])):
                mk = ok & (yrs == yr)
                if mk.sum() < 30:
                    continue
                tot += 1
                wins += int(np.mean((ps[mk] - y[mk]) ** 2) < np.mean((bs[mk] - y[mk]) ** 2))
            br = float(np.mean((ps[ok] - y[ok]) ** 2)) if ok.sum() else None
            bb = float(np.mean((bs[ok] - y[ok]) ** 2)) if ok.sum() else None
            passed = bool(tot) and br is not None and br < bb - 0.002 and wins >= math.ceil(tot * 0.8)
            # 今日の状態の統計（全期間）
            lab = labels[m].values
            cur = now_label[m]
            sel = (lab == cur) & ~np.isnan(fr)
            nn = int(sel.sum())
            upr = float((fr[sel] > 0).mean()) if nn else None
            med = float(np.median(fr[sel])) if nn else None
            base_all = float(np.nanmean(y))
            p_now = ps[-1] if not np.isnan(ps[-1]) else None
            res.setdefault(m, {})[hid] = {"n_now": nn, "up_now": upr, "median_now": (None if med is None else float(math.expm1(med))), "p": (None if p_now is None else float(p_now)),
                                         "base": float(bs[-1]) if not np.isnan(bs[-1]) else base_all, "brier": br, "brier_base": bb, "years_better": wins, "years": tot, "passed": passed}
            if cur != "none":
                stacks.append(ps)
        # 合成：状態が「該当なし」でない手法の平均（なければ基準）
        act = []
        P = np.vstack([per[m][0] for m in NAMES]); B = per["fib"][1]
        Lb = np.vstack([labels[m].values != "none" for m in NAMES])
        with np.errstate(all="ignore"):
            pc = np.where(Lb.sum(0) > 0, np.nansum(np.where(Lb, P, 0), 0) / np.maximum(Lb.sum(0), 1), B)
        ok = mask_eval & ~np.isnan(pc) & ~np.isnan(y)
        yrs = idx.year
        wins = tot = 0
        for yr in sorted(set(yrs[ok])):
            mk = ok & (yrs == yr)
            if mk.sum() < 30:
                continue
            tot += 1
            wins += int(np.mean((pc[mk] - y[mk]) ** 2) < np.mean((B[mk] - y[mk]) ** 2))
        br = float(np.mean((pc[ok] - y[ok]) ** 2)); bb = float(np.mean((B[ok] - y[ok]) ** 2))
        res.setdefault("_composite", {})[hid] = {"p": (None if np.isnan(pc[-1]) else float(pc[-1])), "base": float(B[-1]) if not np.isnan(B[-1]) else None,
                                                 "brier": br, "brier_base": bb, "years_better": wins, "years": tot,
                                                 "passed": bool(tot) and br < bb - 0.002 and wins >= math.ceil(tot * 0.8)}
    return res


def analyze(ohlcv, fx):
    """ohlcv：日足（USD）。fx：円/ドル。戻り値は画面・記録用の辞書。"""
    df = ohlcv.astype(float).copy()
    df = df[~df.index.duplicated()]
    labels, info, piv, cur, thr = compute_labels(df)
    close = df.close
    ev = evaluate(labels, close)
    now_label = {m: labels[m].iloc[-1] for m in NAMES}
    stats = summarize(labels, close, ev, now_label)
    c = float(close.iloc[-1])
    J = lambda x: None if x is None else round(float(x) * fx, 2)
    U = lambda x: None if x is None else round(float(x), 4)

    items = []
    for m in NAMES:
        i = info.get(m, {})
        entry = {"id": m, "name": NAMES[m], "label": now_label[m], "state": _ja(now_label[m]), "stats": stats[m], "levels": {}}
        # 表示用の価格（ドル・円）
        lv = {}
        if m == "fib" and i:
            a, b, kb = i["a"], i["b"], i["kb"]
            rng = abs(b - a)
            out = now_label[m].endswith("|out")
            if kb == "H":
                for r in FIB:
                    lv["戻り%.1f%%" % (r * 100)] = b - r * rng
                if out:   # 起点を割り込んだ：下向きの目安（上げ幅の127.2%・161.8%を高値から引く）
                    lv["下の目安127.2%"] = b - 1.272 * rng; lv["下の目安161.8%"] = b - 1.618 * rng
                else:
                    lv["延長127.2%"] = a + 1.272 * rng; lv["延長161.8%"] = a + 1.618 * rng
            else:
                for r in FIB:
                    lv["戻り%.1f%%" % (r * 100)] = b + r * rng
                if out:
                    lv["上の目安127.2%"] = b + 1.272 * rng; lv["上の目安161.8%"] = b + 1.618 * rng
                else:
                    lv["延長127.2%"] = a - 1.272 * rng; lv["延長161.8%"] = a - 1.618 * rng
            lv["起点"] = a; lv["終点"] = b
            entry["swing"] = {"direction": "上げ" if kb == "H" else "下げ", "ratio": round(float(i["r"]), 3),
                              "from_date": str(df.index[piv[-2][0]].date()), "to_date": str(df.index[piv[-1][0]].date())}
            # 進行中の動きに対する戻り（確定前の高値・安値を使う。表示用）
            tr_, hi_, hi_i, lo_, lo_i = cur
            live = {}
            if tr_ == -1 and piv[-1][2] == "H":
                top = piv[-1][1]; rg = top - lo_
                if rg > 0:
                    live = {"direction": "下げ", "from": top, "to": lo_, "levels": {"戻り%.1f%%" % (r * 100): lo_ + r * rg for r in FIB}}
            elif tr_ == 1 and piv[-1][2] == "L":
                bot = piv[-1][1]; rg = hi_ - bot
                if rg > 0:
                    live = {"direction": "上げ", "from": bot, "to": hi_, "levels": {"押し%.1f%%" % (r * 100): hi_ - r * rg for r in FIB}}
            if live:
                entry["live_swing"] = {"direction": live["direction"], "from": {"usd": U(live["from"]), "jpy": J(live["from"])}, "to": {"usd": U(live["to"]), "jpy": J(live["to"])},
                                       "levels": {k: {"usd": U(v), "jpy": J(v)} for k, v in live["levels"].items()}}
        elif m in ("dbl", "hs", "cup") and i:
            for k, nm in (("neck", "ネックライン"), ("top", "高値"), ("bottom", "安値"), ("head", "ヘッド"), ("rim", "縁"), ("target", "目標値")):
                if k in i:
                    lv[nm] = i[k]
        elif m == "tri" and i:
            lv = {"上限線": i["upper"], "下限線": i["lower"]}
            entry["position"] = round(float(i["pos"]), 2)
        elif m == "sr" and i:
            lv = {k: v for k, v in (("サポート", i.get("support")), ("レジスタンス", i.get("resistance"))) if v is not None}
        elif m == "pivotw" and i:
            lv = {"S2": i["s2"], "S1": i["s1"], "P": i["p"], "R1": i["r1"], "R2": i["r2"]}
        elif m == "donchian" and i:
            lv = {"20日高値": i["high20"], "20日安値": i["low20"], "55日高値": i["high55"], "55日安値": i["low55"]}
        elif m == "vprofile" and i:
            lv = {"POC": i["poc"], "山の上端": i["va_high"], "山の下端": i["va_low"]}
        entry["levels"] = {k: {"usd": U(v), "jpy": J(v)} for k, v in lv.items()}
        items.append(entry)
    pivots = [{"date": str(df.index[p[0]].date()), "kind": p[2], "usd": U(p[1]), "jpy": J(p[1]), "confirmed": str(df.index[p[3]].date())} for p in piv[-8:]]
    return {"date": str(df.index[-1].date()), "close_usd": U(c), "items": items, "composite": stats["_composite"], "pivots": pivots,
            "zigzag_threshold": round(float(thr[-1]), 3),
            "rule": "前進型の検証で、ブライアスコアが基準より0.002以上良く、8割以上の年で基準を上回ったら「合格」。合格しても重みを入れるのは🔬検証・改良チャットの判断（設計書 第13章）。それまでの重みは0（参考表示）",
            "excluded": "エリオット波動・ギャン・ワイコフは、数え方が人によって違い、プログラムで一意に判定できないため対象外"}
