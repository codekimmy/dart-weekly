"""
trend_scan.py — 추세·모멘텀 필터 (로컬 실행, FinanceDataReader 기반)

각 종목의 '지금 상승 추세에 있는가'를 정량화합니다. (예측이 아니라 현재 상태 측정)
    · 200일 이동평균선 위에 있는가              (ma200_above)
    · 52주 신고가 대비 현재 위치(%)             (pos_52w, 100%=신고가)
    · 상대강도: 최근 6개월 수익률 − 지수 수익률  (rs_6m, 양수면 시장 대비 강함)

결과 → trend_signals.json  (대시보드 '추세·모멘텀' 탭)

주의: 추세 지표는 상승이 '유지 중'임을 보여줄 뿐, 하락 전환·개별 악재를 막지 못합니다.
      후보를 좁히는 도구이지 매수 신호가 아닙니다. 투자 자문이 아닙니다.

설치: pip install pandas finance-datareader
사용: python trend_scan.py
"""
import sys
import json
import time
from datetime import datetime, timedelta

import pandas as pd
import FinanceDataReader as fdr

import dart_common as dc

TOP_N = 80              # 저장할 최대 종목 수
MAX_TICKERS = 1200      # 조회할 종목 수 (시총 상위순, 실행시간 조절)


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

    risk = _risk_tickers()
    disc = _disclosure_tickers()
    sectors = _sector_map()

    uni = snap[~snap["Code"].isin(risk)].copy()
    uni = uni[uni["Volume"] > 0]
    uni = uni.sort_values("Marcap", ascending=False).head(MAX_TICKERS)
    print(f"  대상 {len(uni):,}종목 (시총 상위) — 히스토리 조회 시작\n")

    end = datetime.today()
    start = end - timedelta(days=430)
    s_str, e_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    # 지수(코스피) 6개월 수익률 — 상대강도 기준선
    idx_ret = 0
    try:
        ks = fdr.DataReader("KS11", (end - timedelta(days=182)).strftime("%Y-%m-%d"), e_str)["Close"]
        if len(ks) > 1:
            idx_ret = (ks.iloc[-1] / ks.iloc[0] - 1) * 100
        print(f"  지수 6개월 수익률: {idx_ret:+.1f}%\n")
    except Exception as e:
        print(f"  (지수 로드 실패 — 상대강도 기준선 0 적용: {e})\n")

    out, t0 = [], time.time()
    six_m = pd.Timestamp(end - timedelta(days=182))
    for i, (_, row) in enumerate(uni.iterrows(), 1):
        code = row["Code"]
        try:
            h = fdr.DataReader(code, s_str, e_str)
        except Exception:
            continue
        if h is None or len(h) < 200:
            continue
        px = h["Close"].dropna()
        if len(px) < 200:
            continue

        cur = px.iloc[-1]
        ma200 = px.iloc[-200:].mean()
        hi_52w = px.iloc[-min(len(px), 245):].max()
        px6 = px[px.index >= six_m]
        r6 = (cur / px6.iloc[0] - 1) * 100 if len(px6) > 1 else None

        ma200_above = bool(cur > ma200)
        pos_52w = round(float(cur / hi_52w * 100), 1) if hi_52w else None
        rs_6m = round(float(r6 - idx_ret), 1) if r6 is not None else None

        score = 0
        if ma200_above:
            score += 40
        if pos_52w is not None:
            score += pos_52w * 0.4
        if rs_6m is not None:
            score += max(min(rs_6m, 60), -30) * 0.6

        cap = row.get("Marcap")
        out.append({
            "ticker": code, "nm": row["Name"],
            "ma200_above": ma200_above, "pos_52w": pos_52w, "rs_6m": rs_6m,
            "cap": int(cap / 1e8) if pd.notna(cap) and cap else None,
            "sector": sectors.get(code, ""),
            "has_disc": code in disc, "score": round(score, 1),
        })
        if i % 50 == 0:
            el = time.time() - t0
            eta = (len(uni) - i) / (i / el) / 60 if el > 0 else 0
            print(f"  {i:,}/{len(uni):,} ({i/len(uni)*100:.0f}%) · 남은 예상 {eta:.0f}분")

    # 200일선 위 + 상대강도 양수만
    out = [o for o in out if o["ma200_above"] and (o["rs_6m"] or 0) > 0]
    out.sort(key=lambda x: x["score"], reverse=True)
    out = out[:TOP_N]

    payload = {"date": end.strftime("%Y%m%d"), "index_ret_6m": round(idx_ret, 1), "rows": out}
    with open("trend_signals.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)

    print(f"\n저장: trend_signals.json ({len(out)}종목)")
    for s in out[:15]:
        tag = " [공시]" if s["has_disc"] else ""
        print(f"  {s['nm']:12s} 52주 {s['pos_52w']:5.1f}% · RS {s['rs_6m']:+6.1f}%{tag}")


if __name__ == "__main__":
    scan(sys.argv[1] if len(sys.argv) > 1 else None)
