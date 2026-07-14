"""
trend_scan.py — 추세·모멘텀 필터 (로컬 실행, pykrx)

각 종목에 대해 '지금 상승 추세에 있는가'를 정량화합니다. (예측이 아니라 현재 상태 측정)
    · 200일 이동평균선 위에 있는가            (ma200_above)
    · 52주 신고가 대비 현재 위치(%)           (pos_52w: 100%면 신고가)
    · 상대강도: 최근 6개월 수익률 - 지수 수익률 (rs_6m, 양수면 시장 대비 강함)
점수(score)는 세 지표를 합친 대략적 순위용이며, 절대적 매수 근거가 아닙니다.

결과를 trend_signals.json 으로 저장합니다. 대시보드 '추세·모멘텀' 탭에서 읽습니다.

주의: 추세 지표는 상승이 '유지 중'임을 보여줄 뿐, 하락 전환·개별 악재를 막지 못합니다.
      후보를 좁히는 도구이지 매수 신호가 아닙니다. 투자 자문이 아닙니다.

설치: pip install pandas pykrx finance-datareader
사용: python trend_scan.py            (최근 거래일 기준)
      python trend_scan.py 20260710
"""
import sys
import json
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

TOP_N = 80

def _risk_tickers():
    risk = set()
    try:
        import FinanceDataReader as fdr
        adm = fdr.StockListing("KRX-ADMINISTRATIVE")
        col = "Symbol" if "Symbol" in adm.columns else adm.columns[0]
        risk |= set(adm[col].astype(str).str.zfill(6))
    except Exception:
        pass
    try:
        with open("risk_extra.json", encoding="utf-8") as f:
            risk |= set(json.load(f))
    except FileNotFoundError:
        pass
    return risk

def _sector_map():
    try:
        import FinanceDataReader as fdr
        lst = fdr.StockListing("KRX")
        cc = "Code" if "Code" in lst.columns else "Symbol"
        return {str(r[cc]).zfill(6): (r.get("Sector") or r.get("Industry") or "")
                for _, r in lst.iterrows()}
    except Exception:
        return {}

def _disclosure_tickers():
    try:
        with open("disclosures.json", encoding="utf-8") as f:
            return {r.get("ticker") for r in json.load(f).get("rows", []) if r.get("ticker")}
    except Exception:
        return set()

def scan(base_date=None):
    if base_date is None:
        base_date = stock.get_nearest_business_day_in_a_week()
    d0 = datetime.strptime(base_date, "%Y%m%d")
    start = (d0 - timedelta(days=420)).strftime("%Y%m%d")   # 약 14개월(200일선·52주 확보)
    six_m_ago = (d0 - timedelta(days=182)).strftime("%Y%m%d")

    print(f"기준일 {base_date} 추세·모멘텀 스캔 중…")
    risk = _risk_tickers()
    sectors = _sector_map()
    disc = _disclosure_tickers()
    marcap = {}
    try:
        marcap = stock.get_market_cap(base_date)["시가총액"].to_dict()
    except Exception:
        pass

    # 지수(코스피) 6개월 수익률 — 상대강도 기준
    try:
        kospi = stock.get_index_ohlcv_by_date(six_m_ago, base_date, "1001")["종가"]
        idx_ret = (kospi.iloc[-1] / kospi.iloc[0] - 1) * 100 if len(kospi) > 1 else 0
    except Exception:
        idx_ret = 0

    today = stock.get_market_ohlcv(base_date)
    tickers = [t for t in today.index if t not in risk]
    out = []
    for i, tkr in enumerate(tickers):
        try:
            px = stock.get_market_ohlcv(start, base_date, tkr)["종가"].dropna()
        except Exception:
            continue
        if len(px) < 200:
            continue
        cur = px.iloc[-1]
        ma200 = px.iloc[-200:].mean()
        hi_52w = px.iloc[-min(len(px), 245):].max()
        # 6개월 수익률
        px6 = px[px.index >= pd.Timestamp(d0 - timedelta(days=182))]
        r6 = (cur / px6.iloc[0] - 1) * 100 if len(px6) > 1 else None

        ma200_above = cur > ma200
        pos_52w = round(cur / hi_52w * 100, 1) if hi_52w else None
        rs_6m = round(r6 - idx_ret, 1) if r6 is not None else None

        # 대략적 순위 점수: 추세 우위일수록 높게
        score = 0
        if ma200_above: score += 40
        if pos_52w is not None: score += pos_52w * 0.4       # 신고가 근처일수록↑
        if rs_6m is not None: score += max(min(rs_6m, 60), -30) * 0.6
        cap_eok = round(marcap.get(tkr, 0) / 1e8) if marcap.get(tkr) else None

        out.append({
            "ticker": tkr, "nm": stock.get_market_ticker_name(tkr),
            "ma200_above": bool(ma200_above), "pos_52w": pos_52w, "rs_6m": rs_6m,
            "cap": cap_eok, "sector": sectors.get(tkr, ""),
            "has_disc": tkr in disc, "score": round(score, 1),
        })
        if (i + 1) % 200 == 0:
            print(f"  …{i+1}/{len(tickers)} 종목")

    # 200일선 위 + 상대강도 양수인 것 우선, 점수순
    out = [o for o in out if o["ma200_above"] and (o["rs_6m"] or 0) > 0]
    out.sort(key=lambda x: x["score"], reverse=True)
    out = out[:TOP_N]
    payload = {"date": base_date, "index_ret_6m": round(idx_ret, 1), "rows": out}
    with open("trend_signals.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"저장: trend_signals.json ({len(out)}종목)")
    for s in out[:15]:
        tag = " [공시]" if s["has_disc"] else ""
        print(f"  {s['nm']:10s} 52주 {s['pos_52w']}% · RS {s['rs_6m']:+.0f}%{tag}")

if __name__ == "__main__":
    scan(sys.argv[1] if len(sys.argv) > 1 else None)
