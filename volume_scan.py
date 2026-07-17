"""
volume_scan.py — 거래량 급증 종목 스캔 (로컬 실행, FinanceDataReader 기반)

기준:
    · 당일 거래량 >= 직전 20영업일 평균 거래량 x 3배
    · 당일 상승(+)
    · 시가총액 하한 없음
    · 관리종목 등 위험종목 제외

결과 → volume_spikes.json  (대시보드 '거래량 급증' 탭)

주의: 거래량 급증은 급등 초입일 수도, 고점 막차·물량 떠넘기기일 수도 있습니다.
      '매수 신호'가 아니라 '들여다볼 후보'입니다. 투자 자문이 아닙니다.

설치: pip install pandas finance-datareader
사용: python volume_scan.py
"""
import sys
import json
import time
from datetime import datetime, timedelta

import pandas as pd
import FinanceDataReader as fdr

import dart_common as dc

SPIKE_MULT = 3.0        # 거래량 급증 배수
AVG_WINDOW = 20         # 평소 거래량 기준 영업일
TOP_N = 60              # 저장할 최대 종목 수
MAX_CANDIDATES = 800    # 히스토리를 조회할 후보 수 (거래대금 상위순, 실행시간 조절)


def _risk_tickers():
    risk = set()
    try:
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
    """종목코드 → 짧은 업종명 (KRX-DESC 표준산업분류 기반)."""
    return dc.sector_map()


def _disclosure_tickers():
    try:
        with open("disclosures.json", encoding="utf-8") as f:
            return {r.get("ticker") for r in json.load(f).get("rows", []) if r.get("ticker")}
    except Exception:
        return set()


def scan(base_date=None):
    print("전 종목 스냅샷 로드 중…")
    snap = fdr.StockListing("KRX")
    snap["Code"] = snap["Code"].astype(str).str.zfill(6)
    print(f"  {len(snap):,}종목")

    risk = _risk_tickers()
    disc = _disclosure_tickers()
    sectors = _sector_map()

    # 상승 + 거래 있는 종목만, 거래대금 상위로 후보 압축
    cand = snap[(snap["ChagesRatio"] > 0) & (snap["Volume"] > 0)].copy()
    cand = cand[~cand["Code"].isin(risk)]
    cand = cand.sort_values("Amount", ascending=False).head(MAX_CANDIDATES)
    print(f"  후보 {len(cand):,}종목 (상승·거래대금 상위) — 히스토리 조회 시작\n")

    end = datetime.today()
    start = end - timedelta(days=AVG_WINDOW * 2 + 20)
    s_str, e_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    spikes, t0 = [], time.time()
    for i, (_, row) in enumerate(cand.iterrows(), 1):
        code = row["Code"]
        try:
            h = fdr.DataReader(code, s_str, e_str)
        except Exception:
            continue
        if h is None or len(h) < AVG_WINDOW + 1:
            continue
        vol_today = h["Volume"].iloc[-1]
        avg = h["Volume"].iloc[-(AVG_WINDOW + 1):-1].mean()
        if avg <= 0 or vol_today <= 0:
            continue
        mult = vol_today / avg
        if mult >= SPIKE_MULT:
            cap = row.get("Marcap")
            spikes.append({
                "ticker": code,
                "nm": row["Name"],
                "mult": round(float(mult), 1),
                "chg": round(float(row["ChagesRatio"]), 2),
                "close": int(row["Close"]),
                "cap": int(cap / 1e8) if pd.notna(cap) and cap else None,
                "sector": sectors.get(code, ""),
                "has_disc": code in disc,
            })
        if i % 50 == 0:
            el = time.time() - t0
            eta = (len(cand) - i) / (i / el) / 60 if el > 0 else 0
            print(f"  {i:,}/{len(cand):,} ({i/len(cand)*100:.0f}%) · 발견 {len(spikes)}종목 · 남은 예상 {eta:.0f}분")

    spikes.sort(key=lambda x: x["mult"], reverse=True)
    spikes = spikes[:TOP_N]
    base = h.index[-1].strftime("%Y%m%d") if len(spikes) and h is not None else end.strftime("%Y%m%d")
    payload = {"date": base, "criteria": f"거래량 {SPIKE_MULT}배+ · 상승", "rows": spikes}
    with open("volume_spikes.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)

    print(f"\n저장: volume_spikes.json ({len(spikes)}종목)")
    for s in spikes[:15]:
        tag = " [공시]" if s["has_disc"] else ""
        print(f"  {s['nm']:12s} {s['mult']:5.1f}배  +{s['chg']:.1f}%{tag}")


if __name__ == "__main__":
    scan(sys.argv[1] if len(sys.argv) > 1 else None)
