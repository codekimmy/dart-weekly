"""
event_study.py — 이벤트 스터디 백테스트 (로컬 실행, 연도별 분할 처리)

사용법:
    python event_study.py 2024            → 2024년만 수집·계산 (연도 조각 저장)
    python event_study.py 2022 2023 2024  → 여러 해를 차례로 처리
    python event_study.py                 → 이미 만들어진 모든 연도 조각을 합쳐 통계만 생성

동작:
    · 연도별로 _year_2024.csv 같은 조각 파일을 남깁니다. 이미 있으면 그 해는 건너뜁니다.
    · 중간에 끊겨도 그 해의 진행분(_prog_2024.csv)부터 이어서 재개합니다.
    · 마지막에 존재하는 모든 연도 조각을 합쳐 event_study_hist.json 을 만듭니다.
      → 나중에 새 연도만 추가로 돌리면, 기존 연도는 재수집 없이 합산됩니다.

권장: 처음엔 1년(예: 2024)으로 완주해 결과를 확인한 뒤, 연도를 하나씩 늘리세요.
      표본이 적은 유형(20건 미만)은 '표본부족'으로 표시됩니다.

주의: 과거 통계는 미래를 보장하지 않습니다. 투자 자문이 아닙니다.
설치: pip install requests pandas pykrx finance-datareader
"""
import os
import sys
import glob
import json
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock
import dart_common as dc

HORIZONS = [1, 5, 20]
MIN_SAMPLE = 20
YEAR_FILE = "_year_{}.csv"     # 연도별 결과 조각
PROG_FILE = "_prog_{}.csv"     # 연도별 진행 중 저장
DISC_FILE = "_disc_{}.csv"     # 연도별 공시 캐시

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

def fetch_disclosures(bgn_de, end_de):
    rows = []
    chunks = _date_chunks(bgn_de, end_de)
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

def load_full_index(bgn_de, end_de):
    """해당 기간 지수를 시장별로 한 번만 로드 (FDR 우선, pykrx 대체)."""
    lo = bgn_de
    hi = _shift(end_de, 70)
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

def process_year(year):
    """한 해를 수집·계산해 연도 조각(_year_YYYY.csv)을 만든다. 이미 있으면 건너뜀."""
    yf = YEAR_FILE.format(year)
    if os.path.exists(yf):
        print(f"[{year}] 이미 완료된 연도 — 건너뜀 ({yf})")
        return
    bgn, end = f"{year}0101", f"{year}1231"
    today = datetime.today().strftime("%Y%m%d")
    if end > today:
        end = today
    print(f"\n===== {year}년 처리 시작 ({bgn}~{end}) =====")

    # 1-2단계: 공시 수집 + 세부분리 (연도별 캐시)
    dfile = DISC_FILE.format(year)
    if os.path.exists(dfile):
        df = pd.read_csv(dfile, dtype=str)
        print(f"[1-2/3] {year} 공시 캐시 사용 ({len(df):,}건)")
    else:
        print(f"[1/3] {year} 공시 수집")
        df = fetch_disclosures(bgn, end)
        print(f"      {len(df):,}건")
        if df.empty:
            print(f"      {year}년 수집 0건 — 건너뜀")
            return
        print(f"[2/3] {year} 세부 방향 분리")
        df = dc.refine(df, bgn, end)
        df.to_csv(dfile, index=False, encoding="utf-8-sig")
        print(f"      캐시 저장: {dfile}")

    # 3단계: 주가 대조 (진행분 이어서)
    print(f"[3/3] {year} 주가 대조 …")
    load_full_index(bgn, end)
    pf = PROG_FILE.format(year)
    if os.path.exists(pf):
        prev = pd.read_csv(pf)
        recs = prev.to_dict("records")
        done = {r["_key"] for r in recs if "_key" in r}
        print(f"  이전 진행분 {len(recs):,}건 이어서 진행")
    else:
        recs, done = [], set()

    total = len(df)
    t0 = time.time()
    for i, (_, row) in enumerate(df.iterrows(), 1):
        key = f"{row['ticker']}_{row['date']}_{row['sub']}"
        if key in done:
            continue
        er = excess_returns(row)
        if er:
            recs.append({"_key": key, "sub": row["sub"], **er})
        if i % 25 == 0:
            el = time.time() - t0
            rate = i / el if el > 0 else 0
            eta = (total - i) / rate / 60 if rate > 0 else 0
            print(f"  {year} 진행 {i:,}/{total:,} ({i/total*100:.0f}%) · 남은 예상 {eta:.0f}분")
        if i % 100 == 0:
            pd.DataFrame(recs).to_csv(pf, index=False, encoding="utf-8-sig")

    pd.DataFrame(recs).to_csv(yf, index=False, encoding="utf-8-sig")
    if os.path.exists(pf):
        os.remove(pf)
    print(f"[{year}] 완료 → {yf} ({len(recs):,}건)")


def build_hist():
    """존재하는 모든 연도 조각을 합쳐 event_study_hist.json 생성."""
    files = sorted(glob.glob(YEAR_FILE.format("*")))
    if not files:
        print("합칠 연도 조각이 없습니다. 먼저  python event_study.py 2024  처럼 실행하세요.")
        return
    frames = []
    for f in files:
        d = pd.read_csv(f)
        frames.append(d)
        print(f"  합산: {f} ({len(d):,}건)")
    res = pd.concat(frames, ignore_index=True)
    if "_key" in res.columns:
        res = res.drop_duplicates(subset=["_key"]).drop(columns=["_key"])
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
    years = [os.path.basename(f).replace("_year_", "").replace(".csv", "") for f in files]
    print(f"\n저장: event_study_hist.json  (합산 연도: {', '.join(years)} · 총 {len(res):,}건)")
    print("\n=== 유형별 공시 후 초과수익 ===")
    for s_, e in sorted(hist.items(), key=lambda x: (x[1]["t20"] if x[1]["t20"] is not None else -99), reverse=True):
        flag = " (표본부족)" if e["n"] < MIN_SAMPLE else ""
        print(f"  {s_:22s} n={e['n']:5d}  T+20={e['t20']}%  win={e['win']}%{flag}")


def main():
    if dc.API_KEY.startswith("여기에"):
        print("오류: DART_API_KEY 환경변수가 설정되지 않았습니다.")
        print("  Windows(cmd):  set DART_API_KEY=발급받은키")
        return
    years = [a for a in sys.argv[1:] if a.isdigit()]
    for y in years:
        process_year(y)
    build_hist()


if __name__ == "__main__":
    main()
