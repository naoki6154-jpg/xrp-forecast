"""出来事ごとの「その後の値動き」を自動で計算する（設計書 3-3）。

入力：facts/events.csv、data/raw/prices/binance_{xrp,btc}_usdt_1h.csv・_1d.csv（Binance公式一括データ）
出力：facts/reactions.csv（出来事 × 期間 ごとに1行）、facts/reaction_summary.csv（種類ごとの集計）

計算のきまり（時刻のある出来事）：
- 基準の価格：発表の直前の正時の価格（その時刻に始まる1時間足の始値）。例：22:30発表なら22:00。
- h 後の価格：発表から h 以上たった最初の正時の価格。例：22:30発表の1時間後 → 24:00。
- 最大の上昇・下落：基準の時刻から h 後の時刻までの1時間足の高値・安値。
時刻のない出来事（日付のみ）：前日の日足の終値を基準に、d 日後の日足の終値で計算（UTCの日付）。
データの範囲外（まだ時間がたっていない等）の値は空欄。
"""
import csv
import os
import statistics
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTS = os.path.join(ROOT, "facts")
PRICES = os.path.join(ROOT, "data", "raw", "prices")
HORIZONS_H = [("1時間", 1), ("4時間", 4), ("1日", 24), ("1週間", 24 * 7), ("1ヶ月", 24 * 30)]
HORIZONS_D = [("1日", 1), ("1週間", 7), ("1ヶ月", 30)]
FIELDS = ["event_id", "category", "subcategory", "event_time_utc", "basis", "horizon", "base_time", "end_time",
          "xrp_base", "xrp_end", "xrp_ret_pct", "xrp_max_up_pct", "xrp_max_down_pct",
          "btc_ret_pct", "xrp_minus_btc_pct", "price_source"]


def load_1h(sym):
    out = {}
    with open(os.path.join(PRICES, f"binance_{sym}_usdt_1h.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["time"]] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
    return out


def load_1d(sym):
    out = {}
    with open(os.path.join(PRICES, f"binance_{sym}_usdt_1d.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("partial") == "1":
                continue
            out[r["date"]] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
    return out


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def pct(a, b):
    return "" if a is None or b is None else f"{(b / a - 1) * 100:.3f}"


def hourly(ev_t, hours, x1h, b1h):
    base_t = ev_t.replace(minute=0, second=0, microsecond=0)
    end_t = ev_t + timedelta(hours=hours)
    if end_t.minute or end_t.second:
        end_t = end_t.replace(minute=0, second=0) + timedelta(hours=1)
    xb, xe = x1h.get(iso(base_t)), x1h.get(iso(end_t))
    bb, be = b1h.get(iso(base_t)), b1h.get(iso(end_t))
    hi = lo = None
    if xb and xe:
        t = base_t
        highs, lows = [], []
        while t < end_t:
            c = x1h.get(iso(t))
            if c:
                highs.append(c[1]); lows.append(c[2])
            t += timedelta(hours=1)
        hi, lo = (max(highs), min(lows)) if highs else (None, None)
    xr = pct(xb[0], xe[0]) if xb and xe else ""
    br = pct(bb[0], be[0]) if bb and be else ""
    return dict(base_time=iso(base_t), end_time=iso(end_t),
                xrp_base=f"{xb[0]}" if xb else "", xrp_end=f"{xe[0]}" if xe else "", xrp_ret_pct=xr,
                xrp_max_up_pct=pct(xb[0], hi) if xb and hi else "", xrp_max_down_pct=pct(xb[0], lo) if xb and lo else "",
                btc_ret_pct=br, xrp_minus_btc_pct=f"{float(xr) - float(br):.3f}" if xr and br else "",
                price_source="Binance XRPUSDT・BTCUSDT 1時間足（始値）")


def daily(ev_d, days, x1d, b1d):
    base_d = ev_d - timedelta(days=1)
    end_d = ev_d + timedelta(days=days - 1)
    k0, k1 = base_d.isoformat(), end_d.isoformat()
    xb, xe, bb, be = x1d.get(k0), x1d.get(k1), b1d.get(k0), b1d.get(k1)
    hi = lo = None
    if xb and xe:
        cs = [x1d.get((ev_d + timedelta(days=i)).isoformat()) for i in range(days)]
        cs = [c for c in cs if c]
        hi, lo = (max(c[1] for c in cs), min(c[2] for c in cs)) if cs else (None, None)
    xr = pct(xb[3], xe[3]) if xb and xe else ""
    br = pct(bb[3], be[3]) if bb and be else ""
    return dict(base_time=f"{k0} 終値", end_time=f"{k1} 終値",
                xrp_base=f"{xb[3]}" if xb else "", xrp_end=f"{xe[3]}" if xe else "", xrp_ret_pct=xr,
                xrp_max_up_pct=pct(xb[3], hi) if xb and hi else "", xrp_max_down_pct=pct(xb[3], lo) if xb and lo else "",
                btc_ret_pct=br, xrp_minus_btc_pct=f"{float(xr) - float(br):.3f}" if xr and br else "",
                price_source="Binance XRPUSDT・BTCUSDT 日足（UTC、終値）")


def main():
    x1h, b1h, x1d, b1d = load_1h("xrp"), load_1h("btc"), load_1d("xrp"), load_1d("btc")
    with open(os.path.join(FACTS, "events.csv"), newline="", encoding="utf-8") as f:
        events = list(csv.DictReader(f))
    rows = []
    for e in events:
        common = dict(event_id=e["event_id"], category=e["category"], subcategory=e["subcategory"],
                      event_time_utc=e["datetime_utc"] or e["date_et"])
        if e["datetime_utc"]:
            t = datetime.strptime(e["datetime_utc"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
            for name, h in HORIZONS_H:
                rows.append(dict(common, basis="時刻あり（1時間足）", horizon=name, **hourly(t, h, x1h, b1h)))
        else:
            d = datetime.strptime(e["date_et"], "%Y-%m-%d").date()
            for name, n in HORIZONS_D:
                rows.append(dict(common, basis="日付のみ（日足）", horizon=name, **daily(d, n, x1d, b1d)))
    with open(os.path.join(FACTS, "reactions.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["event_time_utc"], r["event_id"],
                                                [h for h, _ in HORIZONS_H].index(r["horizon"]))))

    # 種類 × 期間 ごとの集計（件数も必ず出す）
    groups = {}
    for r in rows:
        if r["xrp_ret_pct"]:
            groups.setdefault((r["subcategory"], r["horizon"]), []).append(r)
    summ = []
    for (sub, hz), rs in sorted(groups.items()):
        xs = [float(r["xrp_ret_pct"]) for r in rs]
        ex = [float(r["xrp_minus_btc_pct"]) for r in rs if r["xrp_minus_btc_pct"]]
        ab = [abs(x) for x in xs]
        summ.append(dict(subcategory=sub, horizon=hz, n=len(xs),
                         up_share_pct=f"{sum(x > 0 for x in xs) / len(xs) * 100:.1f}",
                         median_ret_pct=f"{statistics.median(xs):.3f}", mean_ret_pct=f"{statistics.mean(xs):.3f}",
                         median_abs_ret_pct=f"{statistics.median(ab):.3f}",
                         p10_ret_pct=f"{sorted(xs)[int(len(xs) * 0.1)]:.3f}",
                         p90_ret_pct=f"{sorted(xs)[min(len(xs) - 1, int(len(xs) * 0.9))]:.3f}",
                         median_xrp_minus_btc_pct=f"{statistics.median(ex):.3f}" if ex else "",
                         first=min(r["event_time_utc"] for r in rs)[:10], last=max(r["event_time_utc"] for r in rs)[:10]))
    with open(os.path.join(FACTS, "reaction_summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summ[0]) if summ else ["subcategory"])
        w.writeheader()
        w.writerows(summ)
    print(f"値動き：{len(rows)}行（出来事 {len(events)}件）、集計 {len(summ)}行")
    for s in summ:
        print("  ", s)


if __name__ == "__main__":
    main()
