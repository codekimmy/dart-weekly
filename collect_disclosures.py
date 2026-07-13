"""
collect_disclosures.py — 주간 공시 수집 (DART 전용, 클라우드/Actions에서 실행)

하는 일:
    1) 지난 N일간 상장사 공시를 DART OpenAPI로 수집
    2) 위험 종목 제외 (관리종목: FinanceDataReader / 위험이벤트: DART 보고서명)
    3) 세부 유형 분류 + 세부 방향 분리(유상증자 배정방식, 임원 매수/매도)
    4) 대시보드가 읽는 disclosures.json 으로 저장

pykrx를 쓰지 않으므로 GitHub Actions 클라우드에서도 안정적으로 동작합니다.
(주가가 필요한 통계는 event_study.py 가 로컬에서 따로 생성합니다.)

설치: pip install requests pandas finance-datareader
"""
import json
from datetime import datetime, timedelta

import pandas as pd
import dart_common as dc

LOOKBACK_DAYS = 7  # 최근 며칠치를 볼지

def _risk_tickers():
    """제외할 위험 종목(종목코드 set). 관리종목 목록을 최선껏 수집."""
    risk = set()
    try:
        import FinanceDataReader as fdr
        adm = fdr.StockListing("KRX-ADMINISTRATIVE")  # 관리종목
        col = "Symbol" if "Symbol" in adm.columns else adm.columns[0]
        risk |= set(adm[col].astype(str).str.zfill(6))
    except Exception as e:
        print(f"[risk] 관리종목 목록 수집 실패(무시): {e}")
    # 거래정지/불성실공시 목록은 KRX 정보데이터시스템에서 별도 수집해 아래 파일로 넣어두면 합쳐집니다.
    try:
        with open("risk_extra.json", encoding="utf-8") as f:
            risk |= set(json.load(f))
    except FileNotFoundError:
        pass
    return risk

def collect():
    end = datetime.today()
    bgn = end - timedelta(days=LOOKBACK_DAYS)
    bgn_de, end_de = bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    print(f"수집 기간: {bgn_de} ~ {end_de}")

    rows, risk_event = [], set()
    for corp_cls, market in (("Y", "KOSPI"), ("K", "KOSDAQ")):
        page = 1
        while True:
            r = dc.get("list.json", bgn_de=bgn_de, end_de=end_de, corp_cls=corp_cls,
                       page_no=page, page_count=100)
            if r.get("status") != "000":
                break
            for it in r.get("list", []):
                code = it.get("stock_code")
                if not code:
                    continue
                import re
                if re.search(dc.RISK_PATTERNS, it["report_nm"]):
                    risk_event.add(code)          # DART 위험이벤트 → 제외 대상
                sub = dc.classify(it["report_nm"])
                if sub is None:
                    continue
                rows.append({
                    "rcept_no": it["rcept_no"], "corp_code": it["corp_code"],
                    "ticker": code, "nm": it["corp_name"], "mk": market,
                    "subtype": sub, "report_nm": it["report_nm"],
                })
            if page >= int(r.get("total_page", 1)):
                break
            page += 1

    df = pd.DataFrame(rows)
    if df.empty:
        print("수집된 공시가 없습니다."); _save([]); return

    # 세부 방향 분리
    df = dc.refine(df, bgn_de, end_de)

    # 위험 종목 제외
    risk = _risk_tickers() | risk_event
    before = len(df)
    df = df[~df.ticker.isin(risk)]
    print(f"위험 종목 제외: {before - len(df)}건 걸러냄 (제외 종목 {len(risk)}개)")

    # 대시보드 형식으로 변환
    out = []
    for _, r in df.iterrows():
        out.append({
            "nm": r.nm, "mk": r.mk,
            "cat": dc.category_of(r.sub), "sub": r.sub,
            "ev": r.report_nm, "scale": "",
            "dir": dc.DIR_DEFAULT.get(r.sub, "n"),
        })
    _save(out)
    print(f"저장 완료: disclosures.json ({len(out)}건)")

def _save(items):
    payload = {"updated": datetime.today().strftime("%Y-%m-%d"), "rows": items}
    with open("disclosures.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    collect()
