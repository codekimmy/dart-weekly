"""
volume_scan.py — 거래량 급증 종목 스캔 (로컬 실행, pykrx)

기준:
    · 당일 거래량 >= 최근 20일 평균 거래량 x 3배
    · 당일 등락률 > 0 (상승)
    · 시가총액 하한 없음
    · 관리종목/거래정지 등 위험종목 제외 (risk_extra.json 있으면 합침)

결과를 volume_spikes.json 으로 저장합니다. 대시보드가 '거래량 급증' 탭에서 읽습니다.
공시 리스트(disclosures.json)에도 있는 종목은 대시보드가 '공시 있음' 배지를 달아
재료(공시) + 수급(거래량)이 겹치는 종목을 교차 확인할 수 있게 합니다.

주의: 거래량 급증은 급등 초입일 수도, 고점 막차·물량 떠넘기기일 수도 있습니다.
      '매수 신호'가 아니라 '들여다볼 후보'로만 쓰세요. 투자 자문이 아닙니다.

설치: pip install pandas pykrx finance-datareader
사용: python volume_scan.py            (가장 최근 거래일 기준)
      python volume_scan.py 20260710  (특정 날짜 기준)
"""
import sys
import json
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

SPIKE_MULT = 3.0     # 거래량 급증 배수
AVG_WINDOW = 20      # 평소 거래량 계산 기간(영업일)
TOP_N = 60           # 저장할 최대 종목 수

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
    """이번 주 공시 종목 set (교차 표시용)."""
    try:
        with open("disclosures.json", encoding="utf-8") as f:
            return {r.get("ticker") for r in json.load(f).get("rows", []) if r.get("ticker")}
    except Exception:
        return set()

def scan(base_date=None):
    # 기준일: 인자 없으면 최근 거래일
    if base_date is None:
        base_date = stock.get_nearest_business_day_in_a_week()
    d0 = datetime.strptime(base_date, "%Y%m%d")
    win_start = (d0 - timedelta(days=AVG_WINDOW * 2 + 10)).strftime("%Y%m%d")

    print(f"기준일 {base_date} 거래량 급증 스캔 중…")
    # 당일 전 종목 OHLCV (거래량·등락률·종가 한 번에)
    today = stock.get_market_ohlcv(base_date)          # 인덱스=티커

    risk = _risk_tickers()
    sectors = _sector_map()
    disc = _disclosure_tickers()
    marcap = {}
    try:
        cap = stock.get_market_cap(base_date)          # 티커별 시가총액
        marcap = cap["시가총액"].to_dict()
    except Exception:
        pass

    spikes = []
    tickers = [t for t in today.index if t not in risk]
    for i, tkr in enumerate(tickers):
        row = today.loc[tkr]
        vol = row.get("거래량", 0)
        chg = row.get("등락률", 0)
        if vol <= 0 or chg <= 0:                        # 상승 + 거래 있는 것만
            continue
        try:
            h = stock.get_market_ohlcv(win_start, base_date, tkr)["거래량"]
        except Exception:
            continue
        if len(h) < AVG_WINDOW + 1:
            continue
        avg = h.iloc[-(AVG_WINDOW + 1):-1].mean()       # 당일 제외한 직전 20일 평균
        if avg <= 0:
            continue
        mult = vol / avg
        if mult >= SPIKE_MULT:
            cap_eok = round(marcap.get(tkr, 0) / 1e8) if marcap.get(tkr) else None
            spikes.append({
                "ticker": tkr,
                "nm": stock.get_market_ticker_name(tkr),
                "mult": round(mult, 1),
                "chg": round(float(chg), 2),
                "close": int(row.get("종가", 0)),
                "cap": cap_eok,
                "sector": sectors.get(tkr, ""),
                "has_disc": tkr in disc,
            })
        if (i + 1) % 200 == 0:
            print(f"  …{i+1}/{len(tickers)} 종목 확인")

    spikes.sort(key=lambda x: x["mult"], reverse=True)
    spikes = spikes[:TOP_N]
    payload = {"date": base_date, "criteria": f"거래량 {SPIKE_MULT}배+ · 상승", "rows": spikes}
    with open("volume_spikes.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"저장: volume_spikes.json ({len(spikes)}종목)")
    for s in spikes[:15]:
        tag = " [공시]" if s["has_disc"] else ""
        print(f"  {s['nm']:10s} {s['mult']:4.1f}배  +{s['chg']:.1f}%{tag}")

if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else None
    scan(base)
