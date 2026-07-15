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

def _date_chunks(bgn, end, months=3):
    """조회 기간을 DART 제한(전체조회 시 3개월)에 맞춰 나눔."""
    from datetime import datetime
    s = datetime.strptime(bgn, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    out = []
    while s <= e:
        # s 로부터 약 months개월 뒤(대략 30*months일)까지, 단 end 넘지 않게
        chunk_end = min(e, s + timedelta(days=30 * months))
        out.append((s.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        s = chunk_end + timedelta(days=1)
    return out

def fetch_disclosures():
    rows = []
    chunks = _date_chunks(BGN_DE, END_DE)
    total_steps = len(chunks) * 2
    step = 0
    for corp_cls, market in (("Y", "KOSPI"), ("K", "KOSDAQ")):
        for bgn, end in chunks:
            step += 1
            page = 1
            got = 0
            while True:
                r = dc.get("list.json", bgn_de=bgn, end_de=end, corp_cls=corp_cls,
                           page_no=page, page_count=100)
                if r.get("status") != "000":
                    if r.get("status") not in ("013",):  # 013=데이터 없음(정상)
                        print(f"  [경고] {market} {bgn}~{end}: {r.get('status')} {r.get('message')}")
                    break
                total_page = int(r.get("total_page", 1))
                for it in r.get("list", []):
                    if not it.get("stock_code"):
                        continue
                    sub = dc.classify(it["report_nm"])
                    if sub is None:
                        continue
                    rows.append({"rcept_no": it["rcept_no"], "corp_code": it["corp_code"],
                                 "date": it["rcept_dt"], "ticker": it["stock_code"],
                                 "market": market, "subtype": sub})
                    got += 1
                if page % 20 == 0:
                    print(f"    {market} {bgn[:6]}~{end[:6]}: {page}/{total_page}p (누적 {len(rows):,}건)")
                if page >= total_page:
                    break
                page += 1
                time.sleep(0.25)
            print(f"  [{step}/{total_steps}] {market} {bgn}~{end} 완료 · 이 구간 {got:,}건 (누적 {len(rows):,}건)")
    return pd.DataFrame(rows)

_cache = {}
_IDX_FULL = {}   # {"KOSPI": 종가 시계열, "KOSDAQ": ...}  — 한 번만 로드

def _shift(d, n):
    return (datetime.strptime(d, "%Y%m%d") + timedelta(days=n)).strftime("%Y%m%d")

def _prices(ticker, bgn, end):
    k = ("px", ticker, bgn, end)
    if k not in _cache:
        try: _cache[k] = stock.get_market_ohlcv_by_date(bgn, end, ticker, adjusted=True)["종가"]
        except Exception: _cache[k] = None
        time.sleep(0.15)
    return _cache[k]

def load_full_index():
    """전체 기간 지수를 시장별로 한 번만 로드 (pykrx 실패 시 FinanceDataReader 대체)."""
    lo = BGN_DE
    hi = _shift(END_DE, 70)
    today = datetime.today().strftime("%Y%m%d")
    if hi > today:            # 미래 날짜 요청 방지
        hi = today
    lo_s = f"{lo[:4]}-{lo[4:6]}-{lo[6:]}"
    hi_s = f"{hi[:4]}-{hi[4:6]}-{hi[6:]}"
    for market, code, fdr_code in (("KOSPI", "1001", "KS11"), ("KOSDAQ", "2001", "KQ11")):
        s = None
        # 1순위: FinanceDataReader (문자열 날짜 — 안정적)
        try:
            import FinanceDataReader as fdr
            df = fdr.DataReader(fdr_code, lo_s, hi_s)
            if df is not None and len(df):
                s = df["Close"]
        except Exception as e:
            print(f"  (FDR {market} 실패: {e})")
        # 2순위: pykrx
        if s is None or not len(s):
            for attempt in range(2):
                try:
                    s = stock.get_index_ohlcv_by_date(lo, hi, code)["종가"]
                    if s is not None and len(s):
                        break
                except Exception:
                    pass
                time.sleep(2)
        _IDX_FULL[market] = s
        ok = s is not None and len(s)
        print(f"  지수 로드 {market}: {'OK (' + str(len(s)) + '일)' if ok else '실패 → 초과수익 보정 없이 진행'}")

def excess_returns(row):
    end = _shift(row.date, 60)
    px = _prices(row.ticker, row.date, end)
    if px is None or len(px) < max(HORIZONS) + 2:
        return None
    idx = _IDX_FULL.get(row.market)
    aligned = idx.reindex(px.index).ffill() if idx is not None and len(idx) else None
    base_s = px.iloc[0]
    base_i = aligned.iloc[0] if (aligned is not None and len(aligned)) else None
    out = {}
    for h in HORIZONS:
        if h >= len(px):
            out[h] = None; continue
        rs = px.iloc[h] / base_s - 1
        ri = 0
        if aligned is not None and base_i and h < len(aligned) and pd.notna(aligned.iloc[h]):
            ri = aligned.iloc[h] / base_i - 1
        out[h] = round((rs - ri) * 100, 4)
    return out

def main():
    if dc.API_KEY.startswith("여기에"):
        print("오류: DART_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  Windows(cmd):  set DART_API_KEY=발급받은키   그다음 python event_study.py")
        return

    cache_file = f"_cache_disclosures_{BGN_DE}_{END_DE}.csv"
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file, dtype=str)
        print(f"[1-2/3] 캐시 사용: {cache_file} ({len(df):,}건) — 수집·분류 건너뜀")
        print(f"        (처음부터 다시 하려면 이 파일을 지우세요)")
    else:
        print(f"[1/3] 공시 수집 ({BGN_DE}~{END_DE})")
        df = fetch_disclosures()
        print(f"      {len(df):,}건")
        if df.empty:
            print("수집된 공시가 0건입니다. 인증키가 올바른지, 조회 기간이 맞는지 확인하세요.")
            return

        print("[2/3] 세부 방향 분리")
        df = dc.refine(df, BGN_DE, END_DE)
        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        print(f"      캐시 저장: {cache_file} (다음 실행 때 재사용)")

    print("[3/3] 주가 대조 & 집계 …")
    print("  지수 로드 중…")
    load_full_index()

    prog_file = f"_cache_returns_{BGN_DE}_{END_DE}.csv"
    done = {}
    if os.path.exists(prog_file):
        prev = pd.read_csv(prog_file)
        recs = prev.to_dict("records")
        done = {r["_key"] for r in recs if "_key" in r}
        print(f"  이전 진행분 {len(recs):,}건 이어서 진행 (처음부터 하려면 {prog_file} 삭제)")
    else:
        recs = []

    total = len(df)
    t_start = time.time()
    for i, (_, row) in enumerate(df.iterrows(), 1):
        key = f"{row['ticker']}_{row['date']}_{row['sub']}"
        if key in done:
            continue
        er = excess_returns(row)
        if er:
            recs.append({"_key": key, "sub": row["sub"], **er})
        if i % 25 == 0:
            el = time.time() - t_start
            rate = i / el if el > 0 else 0
            eta = (total - i) / rate / 60 if rate > 0 else 0
            print(f"  진행 {i:,}/{total:,} ({i/total*100:.0f}%) · 남은 예상 {eta:.0f}분")
        if i % 100 == 0:
            pd.DataFrame(recs).to_csv(prog_file, index=False, encoding="utf-8-sig")
    pd.DataFrame(recs).to_csv(prog_file, index=False, encoding="utf-8-sig")

    res = pd.DataFrame(recs)
    if "_key" in res.columns:
        res = res.drop(columns=["_key"])
    # CSV 재로딩 시 컬럼명이 문자열이 되므로 정수 지평 키로 통일 + 숫자 변환
    res = res.rename(columns={str(h): h for h in HORIZONS})
    for h in HORIZONS:
        if h in res.columns:
            res[h] = pd.to_numeric(res[h], errors="coerce")

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
