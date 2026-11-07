"""段階0の追加の取得先（設計書 3・3-2）。

- deribit：オプションの予想変動率（XRP は USDC建ての XRP_USDC オプション、BTC・ETH は DVOL 指数）
- stablecoins：ステーブルコインの供給量（DefiLlama）
- etf_flows：米国の現物ETFの日次の資金の流れ（Farside Investors。BTC・ETH。XRPは Farside が扱っていない）
- xrpl：XRPレジャーの公開ノード（総供給量、盗まれたXRPの追跡）

全ての行に取得元・取得日時・加工前の値を残す。計算で作る値は data/clean/ に別に保存する。
"""
import csv
import html
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta

from common import (RAW, ROOT, http_get, merge_csv, merge_csv_dynamic, ms_to_date, ms_to_iso, now_utc_iso,
                    last_value, UA)

CLEAN = os.path.join(ROOT, "data", "clean")


# ---------- Deribit：オプション ----------
def deribit():
    fetched_at = now_utc_iso()
    today = fetched_at[:10]
    base = "https://www.deribit.com/api/v2/public"
    notes = []
    total = added = 0

    # XRP：USDC建てのオプション（銘柄名が XRP_USDC- で始まるもの）の現在の板の要約
    d = http_get(f"{base}/get_book_summary_by_currency?currency=USDC&kind=option")
    recs = [r for r in d.get("result", []) if str(r.get("instrument_name", "")).startswith("XRP_USDC-")]
    rows = [dict(snapshot_date=today, **{k: v for k, v in r.items() if not isinstance(v, (list, dict))},
                 source="Deribit public/get_book_summary_by_currency currency=USDC kind=option",
                 fetched_at=fetched_at) for r in recs]
    if rows:
        t, a = merge_csv_dynamic(os.path.join(RAW, "deribit", "xrp_options_snapshot.csv"), rows,
                                 ["snapshot_date", "instrument_name"], first_fields=["snapshot_date", "instrument_name"])
        total += t; added += a
        _xrp_atm_iv(recs, today, fetched_at)
    else:
        notes.append("XRPのオプションが見つからない")

    # BTC・ETH：DVOL（Deribitの予想変動率の指数、日足）
    end = int(time.time() * 1000)
    start = end - 1000 * 86400 * 1000
    for cur in ["BTC", "ETH"]:
        d = http_get(f"{base}/get_volatility_index_data?currency={cur}&start_timestamp={start}"
                     f"&end_timestamp={end}&resolution=1D")
        data = d.get("result", {}).get("data", [])
        rows = [dict(date=ms_to_date(x[0]), open=x[1], high=x[2], low=x[3], close=x[4],
                     source=f"Deribit public/get_volatility_index_data {cur} 1D", fetched_at=fetched_at)
                for x in data]
        t, a = merge_csv(os.path.join(RAW, "deribit", f"dvol_{cur.lower()}.csv"), rows, ["date"],
                         ["date", "open", "high", "low", "close", "source", "fetched_at"])
        total += t; added += a
    return dict(rows=total, added=added, last=today,
                note=f"XRPオプション {len(recs)}銘柄" + ("; " + "; ".join(notes) if notes else ""))


def _xrp_atm_iv(recs, today, fetched_at):
    """満期ごとの「行使価格が現在値に一番近いオプション」の予想変動率（計算値。data/clean に保存）"""
    by_exp = {}
    for r in recs:
        m = re.match(r"XRP_USDC-(\d{1,2}[A-Z]{3}\d{2})-([\dd.]+)-([CP])", r["instrument_name"])
        if not m or r.get("mark_iv") in (None, 0):
            continue
        strike = float(m.group(2).replace("d", "."))
        by_exp.setdefault(m.group(1), []).append((strike, m.group(3), float(r["mark_iv"]),
                                                  float(r.get("underlying_price") or 0)))
    rows = []
    for exp, items in by_exp.items():
        und = next((u for _, _, _, u in items if u), 0)
        if not und:
            continue
        strike = min({s for s, _, _, _ in items}, key=lambda s: abs(s - und))
        ivs = [iv for s, _, iv, _ in items if s == strike]
        exp_dt = datetime.strptime(exp, "%d%b%y").replace(hour=8, tzinfo=timezone.utc)
        days = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400
        rows.append(dict(snapshot_date=today, expiry=exp_dt.strftime("%Y-%m-%d"), days_to_expiry=f"{days:.2f}",
                         underlying=und, atm_strike=strike, atm_mark_iv_pct=f"{sum(ivs) / len(ivs):.2f}",
                         n_options=len(ivs), method="行使価格が原資産価格に最も近いコール・プットの mark_iv の平均",
                         source="data/raw/deribit/xrp_options_snapshot.csv から計算", fetched_at=fetched_at))
    if rows:
        merge_csv(os.path.join(CLEAN, "xrp_atm_iv.csv"), rows, ["snapshot_date", "expiry"], list(rows[0]))


# ---------- ステーブルコインの供給量：DefiLlama ----------
STABLES = {"all": "all", "1": "USDT", "2": "USDC"}


def stablecoins():
    fetched_at = now_utc_iso()
    total = added = 0
    for sid, name in STABLES.items():
        url = ("https://stablecoins.llama.fi/stablecoincharts/all" if sid == "all"
               else f"https://stablecoins.llama.fi/stablecoincharts/all?stablecoin={sid}")
        d = http_get(url)
        rows = []
        for x in d:
            circ = (x.get("totalCirculatingUSD") or x.get("totalCirculating") or {}).get("peggedUSD", "")
            rows.append(dict(date=ms_to_date(int(x["date"]) * 1000), stablecoin=name, circulating_usd=circ,
                             source=f"DefiLlama stablecoins API {url.split('llama.fi')[1]}", fetched_at=fetched_at))
        t, a = merge_csv(os.path.join(RAW, "flows", f"stablecoins_{name.lower()}.csv"), rows, ["date"],
                         ["date", "stablecoin", "circulating_usd", "source", "fetched_at"])
        total += t; added += a
    return dict(rows=total, added=added, last=last_value(os.path.join(RAW, "flows", "stablecoins_all.csv"), "date"),
                note="全体（all）・USDT・USDC")


# ---------- 現物ETFの資金の流れ：Farside Investors ----------
FARSIDE = {"btc": "https://farside.co.uk/bitcoin-etf-flow-all-data/",
           "eth": "https://farside.co.uk/ethereum-etf-flow-all-data/"}


def _parse_num(s):
    s = s.replace(",", "").strip()
    if s in ("", "-"):
        return ""
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        return str(-float(s) if neg else float(s))
    except ValueError:
        return ""


def etf_flows():
    fetched_at = now_utc_iso()
    total = added = 0
    notes = []
    for asset, url in FARSIDE.items():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; xrp-forecast-fetcher)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
        tables = re.findall(r"(?is)<table.*?</table>", raw)
        best, header = [], []
        for tb in tables:
            trs = re.findall(r"(?is)<tr.*?</tr>", tb)
            cells = [[html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                      for c in re.findall(r"(?is)<t[hd][^>]*>.*?</t[hd]>", tr)] for tr in trs]
            cells = [c for c in cells if c]
            idx = [i for i, c in enumerate(cells) if re.match(r"^\d{1,2} [A-Z][a-z]{2} \d{4}$", c[0])]
            if len(idx) > len(best):
                best = [cells[i] for i in idx]
                # 見出し：日付の行より上で、銘柄コード（英字）が2つ以上ある一番近い行
                header = []
                for j in range(idx[0] - 1, -1, -1):
                    if sum(bool(re.match(r"^[A-Z]{3,5}$", x)) for x in cells[j]) >= 2:
                        header = cells[j]
                        break
        if not best:
            notes.append(f"{asset}: 表が見つからない")
            continue
        rows = []
        for c in best:
            dt = datetime.strptime(c[0], "%d %b %Y").strftime("%Y-%m-%d")
            row = dict(date=dt)
            for i, v in enumerate(c[1:], start=1):
                col = header[i] if i < len(header) and re.match(r"^[A-Za-z]+$", header[i] or "") else f"col{i}"
                row[f"{col}_raw"] = v
            row["total_usd_m"] = _parse_num(c[-1])
            row.update(source=f"Farside Investors {url}（同社は推定値を含むと注記）", fetched_at=fetched_at)
            rows.append(row)
        t, a = merge_csv_dynamic(os.path.join(RAW, "flows", f"etf_flows_{asset}.csv"), rows, ["date"],
                                 first_fields=["date", "total_usd_m"])
        total += t; added += a
    if total == 0:
        raise RuntimeError("Farsideの表が取れず：" + "; ".join(notes))
    return dict(rows=total, added=added, last=fetched_at[:10],
                note=("XRPのETFはFarsideが扱っていないため未取得" + ("; " + "; ".join(notes) if notes else "")))


# ---------- XRPレジャー：公開ノード ----------
XRPL_NODES = ["https://xrplcluster.com/", "https://s1.ripple.com:51234/", "https://s2.ripple.com:51234/"]
RIPPLE_EPOCH = 946684800  # 2000-01-01T00:00:00Z
# 追いかけるアドレス（出典を確認したものだけ）
WATCH = {
    "rK21t6nGLy4MNDqhvy3pGwcLFy25qgfzMx": "2026-10-29 Binanceハッキングで盗まれたXRPの受取アドレス"
                                         "（Beosin〈TechFlow掲載〉、The Crypto Basic で確認）",
}
FOLLOW_MIN_XRP = 1_000_000  # これ以上を送った先を、次の段として追いかける
MAX_HOPS = 2


def xrpl_rpc(method, params):
    body = json.dumps({"method": method, "params": [params]}).encode()
    last = None
    for node in XRPL_NODES:
        try:
            req = urllib.request.Request(node, data=body, headers={"Content-Type": "application/json", "User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())["result"]
            if res.get("status") == "success" or "error" not in res:
                return res, node
            last = RuntimeError(f"{node}: {res.get('error')}")
            if res.get("error") in ("actNotFound",):
                return res, node
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def _xrp(drops):
    try:
        return int(drops) / 1_000_000
    except (TypeError, ValueError):
        return None


def xrpl():
    fetched_at = now_utc_iso()
    total = added = 0
    # 総供給量（手数料の焼却で少しずつ減る）
    res, node = xrpl_rpc("ledger", {"ledger_index": "validated"})
    lg = res["ledger"]
    row = dict(time=fetched_at, ledger_index=lg.get("ledger_index"), total_coins_drops=lg.get("total_coins"),
               total_coins_xrp=_xrp(lg.get("total_coins")), close_time=lg.get("close_time_human", ""),
               source=f"XRPL公開ノード {node} ledger(validated)", fetched_at=fetched_at)
    t, a = merge_csv(os.path.join(RAW, "onchain", "xrpl_ledger.csv"), [row], ["time"], list(row))
    total += t; added += a

    # 盗まれたXRPの追跡（受取アドレス → 100万XRP以上の送り先 → さらにその送り先、の2段まで）
    tracked = {a: (0, note, "") for a, note in WATCH.items()}
    frontier = list(WATCH)
    transfers, balances = [], []
    for hop in range(MAX_HOPS + 1):
        nxt = []
        for addr in frontier:
            info, node = xrpl_rpc("account_info", {"account": addr, "ledger_index": "validated"})
            bal = _xrp(info.get("account_data", {}).get("Balance")) if "account_data" in info else None
            balances.append(dict(time=fetched_at, address=addr, hop=hop, balance_xrp=bal if bal is not None else "",
                                 parent=tracked[addr][2], note=tracked[addr][1],
                                 source=f"XRPL公開ノード {node} account_info", fetched_at=fetched_at))
            if hop == MAX_HOPS:
                continue
            txs, node = xrpl_rpc("account_tx", {"account": addr, "limit": 200, "forward": False})
            for item in txs.get("transactions", []):
                tx = item.get("tx") or item.get("tx_json") or {}
                meta = item.get("meta") or {}
                if tx.get("TransactionType") != "Payment" or tx.get("Account") != addr:
                    continue
                amt = meta.get("delivered_amount", tx.get("Amount") or tx.get("DeliverMax"))
                if not isinstance(amt, str):
                    continue  # XRP以外（トークン）
                xrp_amt = _xrp(amt)
                when = item.get("close_time_iso") or (
                    datetime.fromtimestamp(int(tx["date"]) + RIPPLE_EPOCH, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if tx.get("date") else "")
                h = item.get("hash") or tx.get("hash", "")
                transfers.append(dict(hash=h, time=when, from_address=addr, to_address=tx.get("Destination", ""),
                                      amount_xrp=xrp_amt, hop=hop, result=meta.get("TransactionResult", ""),
                                      source=f"XRPL公開ノード {node} account_tx", fetched_at=fetched_at))
                dest = tx.get("Destination")
                if xrp_amt and xrp_amt >= FOLLOW_MIN_XRP and dest and dest not in tracked:
                    tracked[dest] = (hop + 1, f"{addr[:8]}… から {xrp_amt:,.0f} XRP を受け取った", addr)
                    nxt.append(dest)
        frontier = nxt
    t, a = merge_csv(os.path.join(RAW, "onchain", "stolen_xrp_transfers.csv"), transfers, ["hash"],
                     ["hash", "time", "from_address", "to_address", "amount_xrp", "hop", "result", "source", "fetched_at"])
    total += t; added += a
    t, a = merge_csv(os.path.join(RAW, "onchain", "stolen_xrp_balances.csv"), balances, ["time", "address"],
                     ["time", "address", "hop", "balance_xrp", "parent", "note", "source", "fetched_at"])
    total += t; added += a
    held = sum(b["balance_xrp"] for b in balances if isinstance(b["balance_xrp"], float))
    return dict(rows=total, added=added, last=fetched_at[:10],
                note=f"追跡アドレス {len(tracked)}件、その残高の合計 {held:,.0f} XRP（エスクローはアドレス未確認のため未取得）")


EXTRA = {"deribit": deribit, "stablecoins": stablecoins, "etf_flows": etf_flows, "xrpl": xrpl}
