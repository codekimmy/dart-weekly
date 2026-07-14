"""
track_prices.py — 이번 주 공시 종목의 '공시 후 실제 주가 반응'을 채워 넣습니다. (로컬 실행)

disclosures.json 의 각 공시에 대해, 공시일(T) 이후 실제로 며칠 지났으면
그 시점까지의 시장초과수익률(T+1/T+5/T+20 중 도래한 것)을 계산해 'actual' 로 기록합니다.
공시 후 아직 날짜가 안 된 지평은 비워 둡니다. 대시보드는 이 값을 과거 평균(hist)과 나란히 보여줍니다.

pykrx를 쓰므로 로컬에서 실행하세요. 공시 며칠 뒤에 다시 돌리면 값이 채워집니다.
설치: pip install pandas pykrx
"""
import json
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

HORIZONS = [1, 5, 20]

def _returns(ticker, market, date):
    """공시일 이후 실제 T+N 시장초과수익률(도래한 것만)."""
    d0 = datetime.strptime(date, "%Y%m%d")
    end = (d0 + timedelta(days=45)).strftime("%Y%m%d")
    try:
        px = stock.get_market_ohlcv_by_date(date, end, ticker, adjusted=True)["종가"].reset_index(drop=True)
        idx = stock.get_index_ohlcv_by_date(date, end, "1001" if market == "KOSPI" else "2001")["종가"].reset_index(drop=True)
    except Exception:
        return {}
    if len(px) < 2:
        return {}
    out = {}
    for h in HORIZONS:
        if h < len(px):
            rs = px.iloc[h] / px.iloc[0] - 1
            ri = (idx.iloc[h] / idx.iloc[0] - 1) if h < len(idx) else 0
            out[f"t{h}"] = round((rs - ri) * 100, 1)
    return out

def main():
    with open("disclosures.json", encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("rows", [])
    n_filled = 0
    for r in rows:
        tkr, mk, dt = r.get("ticker"), r.get("mk"), r.get("date")
        if not (tkr and dt):
            continue
        act = _returns(tkr, mk, dt)
        if act:
            r["actual"] = act
            n_filled += 1
    with open("disclosures.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"실제 주가 반응 채움: {n_filled}/{len(rows)}건 → disclosures.json 갱신")

if __name__ == "__main__":
    main()
