"""
event_study.py — 이벤트 스터디 백테스트 (로컬 PC에서 실행)

하는 일:
    과거 공시를 전수 수집(DART) → 세부 유형 분리 → 각 공시일(T) 기준 T+1/T+5/T+20
    영업일의 시장초과수익률을 pykrx 주가로 계산 → 유형별 평균·상승확률 집계 →
    event_study_hist.json 으로 저장. 이 JSON을 대시보드가 통계로 사용합니다.

pykrx가 KRX/네이버를 스크래핑하므로 클라우드보다 '내 PC'에서 돌리는 것을 권장합니다.
자주 바뀌는 값이 아니므로 한 달~분기에 한 번만 갱신해도 충분합니다.

설치: pip install requests pandas pykrx
"""
import os
import json
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock
import dart_common as dc

BGN_DE, END_DE = "20220101", "20251231"
HORIZONS = [1, 5, 20]
MIN_SAMPLE = 20

def fetch_disclosures():
    rows = []
    for corp_cls, market in (("Y", "KOSPI"), ("K", "KOSDAQ")):
        page = 1
        while True:
            r = dc.get("list.json", bgn_de=BGN_DE, end_de=END_DE, corp_cls=corp_cls,
                       page_no=page, page_count=100)
            if r.get("status") != "000":
                break
            for it in r.get("list", []):
                if not it.get("stock_code"):
                    continue
                sub = dc.classify(it["report_nm"])
                if sub is None:
                    continue
                rows.append({"rcept_no": it["rcept_no"], "corp_code": it["corp_code"],
                             "date": it["rcept_dt"], "ticker": it["stock_code"],
                             "market": market, "subtype": sub})
            if page >= int(r.get("total_page", 1)):
                break
            page += 1
            time.sleep(0.25)
    return pd.DataFrame(rows)

_cache = {}
def _shift(d, n):
    return (datetime.strptime(d, "%Y%m%d") + timedelta(days=n)).strftime("%Y%m%d")

def _prices(ticker, bgn, end):
    k = ("px", ticker, bgn, end)
    if k not in _cache:
        try: _cache[k] = stock.get_market_ohlcv_by_date(bgn, end, ticker, adjusted=True)["종가"]
        except Exception: _cache[k] = None
        time.sleep(0.15)
    return _cache[k]

def _index(market, bgn, end):
    k = ("idx", market, bgn, end)
    if k not in _cache:
        try: _cache[k] = stock.get_index_ohlcv_by_date(bgn, end, "1001" if market == "KOSPI" else "2001")["종가"]
        except Exception: _cache[k] = None
        time.sleep(0.15)
    return _cache[k]

def excess_returns(row):
    end = _shift(row.date, 60)
    px = _prices(row.ticker, row.date, end)
    if px is None or len(px) < max(HORIZONS) + 2:
        return None
    idx = _index(row.market, row.date, end)
    px = px.reset_index(drop=True)
    base_s = px.iloc[0]
    if idx is not None and len(idx):
        idx = idx.reset_index(drop=True); base_i = idx.iloc[0]
    else:
        idx, base_i = None, None
    out = {}
    for h in HORIZONS:
        if h >= len(px): out[h] = None; continue
        rs = px.iloc[h] / base_s - 1
        ri = (idx.iloc[h] / base_i - 1) if (idx is not None and h < len(idx) and base_i) else 0
        out[h] = round((rs - ri) * 100, 4)
    return out

def main():
    print(f"[1/3] 공시 수집 ({BGN_DE}~{END_DE})")
    df = fetch_disclosures()
    print(f"      {len(df):,}건")

    print("[2/3] 세부 방향 분리")
    df = dc.refine(df, BGN_DE, END_DE)

    print("[3/3] 주가 대조 & 집계 …")
    recs = []
    for _, row in df.iterrows():
        er = excess_returns(row)
        if er: recs.append({"sub": row["sub"], **er})
    res = pd.DataFrame(recs)

    hist = {}
    for sub, g in res.groupby("sub"):
        entry = {"n": int(len(g))}
        for h in HORIZONS:
            col = g[h].dropna()
            entry[f"t{h}"] = round(col.mean(), 1) if len(col) else None
        last = g[HORIZONS[-1]].dropna()
        entry["win"] = round((last > 0).mean() * 100) if len(last) else None
        hist[sub] = entry

    with open("event_study_hist.json", "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    print("저장: event_study_hist.json")
    for s, e in sorted(hist.items(), key=lambda x: (x[1]["t20"] or -99), reverse=True):
        flag = " (표본부족)" if e["n"] < MIN_SAMPLE else ""
        print(f"  {s:22s} n={e['n']:4d}  T+20={e['t20']}%  win={e['win']}%{flag}")

if __name__ == "__main__":
    main()
