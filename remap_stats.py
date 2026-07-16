"""
remap_stats.py — 새 분류 규칙을 기존 백테스트에 적용 (주가 재계산 없음)

왜 필요한가:
    분류 규칙이 바뀌었지만(정정 제외, 자회사·사후공시 분리), 주가 수익률은 종목·날짜만
    있으면 되고 분류와 무관합니다. 그래서 공시만 다시 받아(연도당 40분) 기존 _year_*.csv 의
    수익률에 새 분류를 붙입니다. 몇 시간짜리 주가 재수집을 건너뜁니다.

하는 일:
    1) 해당 연도 공시를 새 규칙으로 다시 수집 → _disc2_YYYY.csv
    2) 기존 _year_YYYY.csv 에서 (종목, 날짜) → 수익률 추출
    3) 둘을 (종목, 날짜)로 join → 새 분류 기준 통계 → event_study_hist.json

사용:
    python remap_stats.py 2024 2025      # 해당 연도들 재분류 + 합산
    python remap_stats.py                # 이미 만든 _remap_*.csv 만 합쳐 통계 재생성

주의: 기존 _year_*.csv 는 그대로 둡니다(삭제 금지). 결과는 _remap_YYYY.csv 로 따로 저장됩니다.
"""
import os
import sys
import glob
import json
import time
from datetime import datetime, timedelta

import pandas as pd
import dart_common as dc

HORIZONS = [1, 5, 20]
MIN_SAMPLE = 20


def _date_chunks(bgn, end, months=3):
    s = datetime.strptime(bgn, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    out = []
    while s <= e:
        ce = min(e, s + timedelta(days=30 * months))
        out.append((s.strftime("%Y%m%d"), ce.strftime("%Y%m%d")))
        s = ce + timedelta(days=1)
    return out


def collect_year(year):
    """새 규칙으로 해당 연도 공시 재수집 (주가 조회 없음)."""
    f = f"_disc2_{year}.csv"
    if os.path.exists(f):
        print(f"[{year}] 재수집 캐시 사용 ({f})")
        return pd.read_csv(f, dtype=str)

    bgn, end = f"{year}0101", f"{year}1231"
    today = datetime.today().strftime("%Y%m%d")
    if end > today:
        end = today

    rows = []
    chunks = _date_chunks(bgn, end)
    steps = len(chunks) * 2
    step = 0
    for corp_cls, market in (("Y", "KOSPI"), ("K", "KOSDAQ")):
        for cb, ce in chunks:
            step += 1
            page = 1
            while True:
                r = dc.get("list.json", bgn_de=cb, end_de=ce, corp_cls=corp_cls,
                           page_no=page, page_count=100)
                if r.get("status") != "000":
                    if r.get("status") != "013":
                        print(f"  [경고] {market} {cb}~{ce}: {r.get('status')} {r.get('message')}")
                    break
                total_page = int(r.get("total_page", 1))
                for it in r.get("list", []):
                    if not it.get("stock_code"):
                        continue
                    sub = dc.classify(it["report_nm"])   # ← 새 규칙
                    if sub is None:
                        continue
                    rows.append({
                        "rcept_no": it["rcept_no"], "corp_code": it["corp_code"],
                        "date": it["rcept_dt"], "ticker": it["stock_code"],
                        "market": market, "subtype": sub,
                        "report_nm": it["report_nm"],   # 향후 재분류용으로 보존
                    })
                if page >= total_page:
                    break
                page += 1
                time.sleep(0.25)
            print(f"  [{step}/{steps}] {market} {cb}~{ce} · 누적 {len(rows):,}건")

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    print(f"[{year}] 세부 방향 분리…")
    df = dc.refine(df, bgn, end)
    df.to_csv(f, index=False, encoding="utf-8-sig")
    print(f"[{year}] 재수집 저장: {f} ({len(df):,}건)")
    return df


def load_returns(year):
    """기존 _year_YYYY.csv 에서 (종목,날짜) → 수익률 추출."""
    f = f"_year_{year}.csv"
    if not os.path.exists(f):
        print(f"[{year}] {f} 없음 — 이 연도는 주가 재사용 불가 (건너뜀)")
        return None
    d = pd.read_csv(f)
    # _key = "종목_날짜_유형" 에서 종목·날짜 분리
    parts = d["_key"].astype(str).str.split("_", n=2, expand=True)
    d["ticker"] = parts[0]
    d["date"] = parts[1]
    d = d.rename(columns={str(h): h for h in HORIZONS})
    keep = ["ticker", "date"] + [h for h in HORIZONS if h in d.columns]
    d = d[keep].drop_duplicates(subset=["ticker", "date"])
    print(f"[{year}] 기존 수익률 재사용: {len(d):,}개 (종목,날짜) 쌍")
    return d


def remap_year(year):
    out = f"_remap_{year}.csv"
    if os.path.exists(out):
        print(f"[{year}] 이미 재분류 완료 — 건너뜀 ({out})")
        return
    disc = collect_year(year)
    if disc is None or disc.empty:
        print(f"[{year}] 공시 없음")
        return
    rets = load_returns(year)
    if rets is None:
        return
    disc = disc[["ticker", "date", "sub"]].copy()
    merged = disc.merge(rets, on=["ticker", "date"], how="inner")
    merged.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[{year}] 재분류 완료 → {out} ({len(merged):,}건 매칭)")


def build_hist():
    files = sorted(glob.glob("_remap_*.csv"))
    if not files:
        print("재분류 결과가 없습니다. 먼저  python remap_stats.py 2024  처럼 실행하세요.")
        return
    frames = []
    for f in files:
        d = pd.read_csv(f)
        frames.append(d)
        print(f"  합산: {f} ({len(d):,}건)")
    res = pd.concat(frames, ignore_index=True)
    res = res.rename(columns={str(h): h for h in HORIZONS})
    for h in HORIZONS:
        if h in res.columns:
            res[h] = pd.to_numeric(res[h], errors="coerce")

    hist = {}
    for sub, g in res.groupby("sub"):
        e = {"n": int(len(g))}
        for h in HORIZONS:
            col = g[h].dropna() if h in g.columns else pd.Series(dtype=float)
            e[f"t{h}"] = round(col.mean(), 1) if len(col) else None
        last = g[HORIZONS[-1]].dropna() if HORIZONS[-1] in g.columns else pd.Series(dtype=float)
        e["win"] = round((last > 0).mean() * 100) if len(last) else None
        hist[sub] = e

    with open("event_study_hist.json", "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    years = [os.path.basename(f).replace("_remap_", "").replace(".csv", "") for f in files]
    print(f"\n저장: event_study_hist.json (연도: {', '.join(years)} · 총 {len(res):,}건)")
    print("\n=== 유형별 공시 후 초과수익 (새 분류) ===")
    for s_, e in sorted(hist.items(), key=lambda x: (x[1]["t20"] if x[1]["t20"] is not None else -99),
                        reverse=True):
        flag = " (표본부족)" if e["n"] < MIN_SAMPLE else ""
        print(f"  {s_:22s} n={e['n']:5d}  T+20={e['t20']}%  win={e['win']}%{flag}")


if __name__ == "__main__":
    if dc.API_KEY.startswith("여기에"):
        print("DART_API_KEY 환경변수를 설정하세요.")
        sys.exit()
    for y in [a for a in sys.argv[1:] if a.isdigit()]:
        remap_year(y)
    build_hist()
