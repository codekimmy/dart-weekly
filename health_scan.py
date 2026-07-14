"""
health_scan.py — 재무 건전성 필터 (DART 재무제표)

관심 종목들의 최근 재무를 뽑아 '장기 보유해도 될 만큼 탄탄한가'를 점검합니다.
    · 매출·영업이익 증가 여부   (전년 대비)
    · 부채비율                  (부채총계 / 자본총계)
    · 영업이익 흑자 여부
급등 예측이 아니라 '지뢰(부실)를 거르는' 용도입니다.

대상: disclosures.json 에 등장한 종목 + trend_signals.json 상위 종목 (있으면).
      전 종목을 도는 대신 이미 후보로 좁혀진 종목만 조회해 호출을 아낍니다.
결과를 health_signals.json 으로 저장합니다.

주의: 재무는 과거 실적이라 미래를 보장하지 않고, 분기 시차가 있습니다. 투자 자문이 아닙니다.
설치: pip install requests pandas
사용: python health_scan.py       (DART_API_KEY 환경변수 필요, event_study.py와 동일 키)
"""
import json
import dart_common as dc

YEAR = "2024"        # 사업보고서 기준 연도 (가장 최근 확정 사업연도)
REPORT = "11011"     # 11011=사업보고서

def _candidates():
    tickers = {}
    for fn in ("disclosures.json", "trend_signals.json", "volume_spikes.json"):
        try:
            with open(fn, encoding="utf-8") as f:
                for r in json.load(f).get("rows", []):
                    if r.get("ticker") and r.get("corp_code"):
                        tickers[r["ticker"]] = r["corp_code"]
                    elif r.get("ticker"):
                        tickers.setdefault(r["ticker"], None)
        except Exception:
            pass
    return tickers

def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None

def fetch_financials(corp_code):
    """단일회사 주요 재무계정 (당기/전기)."""
    r = dc.get("fnlttSinglAcnt.json", corp_code=corp_code, bsns_year=YEAR, reprt_code=REPORT)
    if r.get("status") != "000":
        return None
    acc = {}
    for it in r.get("list", []):
        nm = it.get("account_nm", "")
        acc[nm] = {"cur": _num(it.get("thstrm_amount")), "pre": _num(it.get("frmtrm_amount"))}
    def g(*names):
        for n in names:
            for k, v in acc.items():
                if n in k:
                    return v
        return {"cur": None, "pre": None}
    sales = g("매출액", "수익(매출액)", "영업수익")
    op = g("영업이익")
    debt = g("부채총계")
    equity = g("자본총계")
    out = {}
    if sales["cur"] and sales["pre"]:
        out["sales_growth"] = round((sales["cur"] / sales["pre"] - 1) * 100, 1)
    if op["cur"] and op["pre"]:
        out["op_growth"] = round((op["cur"] / op["pre"] - 1) * 100, 1)
    if op["cur"] is not None:
        out["op_positive"] = op["cur"] > 0
    if debt["cur"] and equity["cur"] and equity["cur"] != 0:
        out["debt_ratio"] = round(debt["cur"] / equity["cur"] * 100, 1)
    return out or None

def main():
    if dc.API_KEY.startswith("여기에"):
        print("DART_API_KEY 환경변수를 설정하세요."); return
    cands = _candidates()
    print(f"재무 점검 대상 {len(cands)}종목")
    rows = []
    for tkr, corp in cands.items():
        if not corp:
            continue  # corp_code 없는 종목은 스킵(수집 JSON에 corp_code가 있어야 함)
        fin = fetch_financials(corp)
        if not fin:
            continue
        # 종합 판정: 매출·영업이익 성장 + 흑자 + 부채비율 200% 미만이면 '양호'
        healthy = (fin.get("op_positive") and
                   (fin.get("sales_growth", 0) or 0) > 0 and
                   (fin.get("debt_ratio", 999) or 999) < 200)
        rows.append({"ticker": tkr, **fin, "healthy": bool(healthy)})
    payload = {"year": YEAR, "rows": rows}
    with open("health_signals.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"저장: health_signals.json ({len(rows)}종목)")

if __name__ == "__main__":
    main()
