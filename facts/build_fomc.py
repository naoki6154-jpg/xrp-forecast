"""FOMC（米国の金融政策会合）を、過去事実データベースに登録する（設計書 3-3）。

- 会合の日程：FRB の公式ページ（一次情報）
    https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm （直近の数年＋今後）
    https://www.federalreserve.gov/monetarypolicy/fomchistoricalYYYY.htm （それより前）
- 決定内容：FRED の誘導目標 DFEDTARU・DFEDTARL（一次データ）の会合前後の変化から判定
- 公表時刻：定例会合は 14:00 ET（FRBの慣例）。臨時会合は時刻を確認できていないので日付のみ。
- 会合直前の利上げ・利下げの織り込み確率は、無料で過去分を取れる一次データがないため空欄。
"""
import csv
import html
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "fetchers"))
from common import merge_csv, now_utc_iso  # noqa: E402
from build_macro import EVENT_FIELDS, VALUE_FIELDS  # noqa: E402

ET = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")
FIRST_YEAR = 2016
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"], 1)}
MON_RE = "|".join(MONTHS)
MON_ANY = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|" \
          r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?"
DASH = r"\s*[-\u2013\u2014]\s*"


def mon(name):
    return MONTHS[next(m for m in MONTHS if m.startswith(name.rstrip(".")[:3]))]


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "xrp-forecast-facts"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    txt = re.sub(r"<[^>]+>", "\n", raw)
    txt = html.unescape(txt)
    return re.sub(r"[ \t\xa0]+", " ", txt)


def parse_calendars(txt):
    """fomccalendars.htm：『2024 FOMC Meetings』の見出しの後に『January』『30-31』…と並ぶ"""
    out = []
    lines = [l.strip() for l in txt.split("\n") if l.strip()]
    year = None
    i = 0
    while i < len(lines):
        m = re.match(r"^(\d{4}) FOMC Meetings$", lines[i])
        if m:
            year = int(m.group(1))
            i += 1
            continue
        mm = re.match(rf"^({MON_ANY})(?:\s*/\s*({MON_ANY}))?$", lines[i])
        if year and mm and i + 1 < len(lines):
            dm = re.match(rf"^(\d{{1,2}})(?:{DASH}(\d{{1,2}}))?(\*)?\s*(\(.*\))?$", lines[i + 1])
            if dm:
                m1 = mon(mm.group(1))
                m2 = mon(mm.group(2)) if mm.group(2) else m1
                d1 = int(dm.group(1))
                d2 = int(dm.group(2)) if dm.group(2) else d1
                note = (dm.group(4) or "").strip("() ").lower()
                start = date(year, m1, d1)
                end = date(year + (1 if m2 < m1 else 0), m2, d2)
                out.append(dict(start=start, end=end, sep=bool(dm.group(3)), note=note,
                                src="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"))
                i += 2
                continue
        i += 1
    return out


def parse_historical(txt, year):
    """fomchistoricalYYYY.htm：『January 30-31 Meeting - 2018』『April 30-May 1 Meeting - 2019』など"""
    out = []
    pat = re.compile(rf"({MON_ANY}) (\d{{1,2}})(?:{DASH}(?:({MON_ANY}) )?(\d{{1,2}}))?"
                     rf"\s*(\([^)]*\))?\s*(Meeting|Conference Call|Unscheduled Meeting)?\s*-\s*(\d{{4}})")
    for m in pat.finditer(txt):
        if int(m.group(7)) != year:
            continue
        note = (m.group(5) or "").strip("() ").lower()
        kind = m.group(6) or ""
        if not kind and not note:
            continue
        if "cancel" in note:
            continue  # 中止された会合
        m1 = mon(m.group(1))
        m2 = mon(m.group(3)) if m.group(3) else m1
        d1 = int(m.group(2))
        d2 = int(m.group(4)) if m.group(4) else d1
        if kind and kind != "Meeting":
            note = (note + " " + kind.lower()).strip()
        out.append(dict(start=date(year, m1, d1), end=date(year, m2, d2), sep=False, note=note,
                        src=f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"))
    return out


def load_series(sid):
    p = os.path.join(ROOT, "data", "raw", "fred", f"{sid}.csv")
    with open(p, newline="", encoding="utf-8") as f:
        return {r["date"]: float(r["value"]) for r in csv.DictReader(f) if r["value"]}


def value_on(series, d):
    """d 以前で一番近い日の値"""
    for k in range(10):
        s = (d - timedelta(days=k)).isoformat()
        if s in series:
            return series[s]
    return None


def main():
    meetings = parse_calendars(fetch_text("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"))
    cal_years = {m["start"].year for m in meetings}
    print(f"カレンダーのページ：{sorted(cal_years)} の {len(meetings)}件")
    for y in range(FIRST_YEAR, datetime.now().year + 1):
        if y in cal_years:
            continue
        try:
            got = parse_historical(fetch_text(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm"), y)
            print(f"過去のページ {y}：{len(got)}件")
            meetings += got
        except Exception as e:  # noqa: BLE001
            print(f"過去のページ {y}：取得できず {e}")
    # 重複を除く
    uniq = {}
    for m in meetings:
        uniq.setdefault((m["start"], m["end"]), m)
    meetings = sorted(uniq.values(), key=lambda m: m["end"])

    upper, lower = load_series("DFEDTARU"), load_series("DFEDTARL")
    today = datetime.now(timezone.utc).date()
    recorded_at = now_utc_iso()
    events, values = [], []
    for m in meetings:
        if m["end"].year < FIRST_YEAR or m["end"] >= today:
            continue
        before_u, after_u = value_on(upper, m["start"] - timedelta(days=1)), value_on(upper, m["end"] + timedelta(days=3))
        before_l, after_l = value_on(lower, m["start"] - timedelta(days=1)), value_on(lower, m["end"] + timedelta(days=3))
        if before_u is None or after_u is None:
            decision = ""
        else:
            chg = round((after_u - before_u) * 100)
            decision = "据え置き" if chg == 0 else (f"利上げ +{chg}bp" if chg > 0 else f"利下げ {chg}bp")
        scheduled = m["note"] in ("",)
        if not scheduled and decision in ("", "据え置き"):
            continue  # 臨時の電話会議で金利を動かさなかったものは登録しない
        ann = m["end"]
        if not scheduled and before_u is not None:
            # 臨時会合：発表日 ＝ 誘導目標（FRED）が変わった日の前日（変更は発表の翌日から適用される）
            d = m["start"]
            while d <= m["end"] + timedelta(days=3):
                v = upper.get(d.isoformat())
                if v is not None and v != before_u:
                    ann = max(m["start"], d - timedelta(days=1))
                    break
                d += timedelta(days=1)
        eid = f"fomc-{ann}"
        if scheduled:
            t = datetime(m["end"].year, m["end"].month, m["end"].day, 14, 0, tzinfo=ET)
            dt_utc = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            dt_jst = t.astimezone(JST).strftime("%Y-%m-%d %H:%M")
            basis = "定例会合の声明の公表時刻 14:00 ET（FRBの慣例。個別の時刻は未確認）"
        else:
            dt_utc, dt_jst = "", ""
            basis = ("臨時会合。発表日は誘導目標（FRED）が変わった日の前日。時刻は未確認のため日付のみ（値動きは日足で計算）")
        span = f"{m['start']}〜{m['end']}" if m["start"] != m["end"] else f"{m['end']}"
        events.append(dict(event_id=eid, category="金融政策", subcategory="fomc",
                           name=f"FOMC（{span}{'、臨時' if not scheduled else ''}）：{decision or '判定できず'}",
                           date_et=ann.isoformat(), datetime_utc=dt_utc, datetime_jst=dt_jst,
                           time_basis=basis, reference_period="",
                           details=("経済見通し（SEP）あり" if m["sep"] else "") + (f" {m['note']}" if m["note"] else ""),
                           source="FRB FOMC calendars（日程）、FRED DFEDTARU/DFEDTARL（決定）",
                           source_url=m["src"], recorded_at=recorded_at))
        if before_u is not None and after_u is not None:
            values += [
                dict(event_id=eid, metric="誘導目標 上限", value=f"{after_u:.2f}", previous=f"{before_u:.2f}",
                     consensus="", surprise="", unit="%", method="会合開始の前日と、終了の3日後の値",
                     source="FRED DFEDTARU"),
                dict(event_id=eid, metric="誘導目標 下限", value=f"{after_l:.2f}" if after_l is not None else "",
                     previous=f"{before_l:.2f}" if before_l is not None else "", consensus="", surprise="",
                     unit="%", method="会合開始の前日と、終了の3日後の値", source="FRED DFEDTARL"),
                dict(event_id=eid, metric="変更幅", value=f"{round((after_u - before_u) * 100)}", previous="",
                     consensus="", surprise="", unit="bp", method="上限の差", source="FRED DFEDTARU"),
            ]
    merge_csv(os.path.join(ROOT, "facts", "events.csv"), events, ["event_id"], EVENT_FIELDS)
    merge_csv(os.path.join(ROOT, "facts", "event_values.csv"), values, ["event_id", "metric"], VALUE_FIELDS)
    print(f"登録：FOMC {len(events)}件")
    for e in events[-6:]:
        print("  ", e["name"], e["datetime_jst"])


if __name__ == "__main__":
    main()
