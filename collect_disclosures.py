"""
collect_disclosures.py — 주간 공시 수집 (DART 전용, 클라우드/Actions에서 실행)

하는 일:
    1) 최근 N일 공시 수집 (DART OpenAPI)
    2) 위험 종목 제외 (관리종목: FinanceDataReader / 위험이벤트: DART 보고서명)
    3) 세부 유형 분류 + 세부 방향 분리(유상증자 배정방식, 임원 매수/매도)
    4) 각 공시에 부가정보 추가:
         · link    : DART 원문 뷰어 링크 (rcept_no 기반)
         · sector  : 업종 (FinanceDataReader)
         · scale   : 시가총액 (억원, FinanceDataReader 시총 기반)
         · repeat  : 최근 3개월 내 같은 종목·같은 유형 재등장 횟수
    5) 대시보드가 읽는 disclosures.json 으로 저장

DART 전용이라 GitHub Actions 클라우드에서도 안정적으로 동작합니다.
(FinanceDataReader의 업종·시총·관리종목 조회는 실패해도 무시하고 넘어갑니다.)

설치: pip install requests pandas finance-datareader
"""
import json
import re
from datetime import datetime, timedelta

import pandas as pd
import dart_common as dc

LOOKBACK_DAYS = 7        # 이번 주 리포트 대상 기간
REPEAT_WINDOW_DAYS = 90  # 연속·중복 공시를 따질 과거 기간 (DART 전체조회 한도 = 3개월)


def _risk_tickers():
    """제외할 위험 종목(종목코드 set)."""
    risk = set()
    try:
        import FinanceDataReader as fdr
        adm = fdr.StockListing("KRX-ADMINISTRATIVE")
        col = "Symbol" if "Symbol" in adm.columns else adm.columns[0]
        risk |= set(adm[col].astype(str).str.zfill(6))
    except Exception as e:
        print(f"[risk] 관리종목 목록 수집 실패(무시): {e}")
    try:
        with open("risk_extra.json", encoding="utf-8") as f:
            risk |= set(json.load(f))
    except FileNotFoundError:
        pass
    return risk


def _sector_marcap():
    """종목코드 → (업종, 시가총액[억원]) 매핑. 실패 시 빈 dict."""
    info = {}
    try:
        import FinanceDataReader as fdr
        lst = fdr.StockListing("KRX")
        code_col = "Code" if "Code" in lst.columns else "Symbol"
        for _, r in lst.iterrows():
            code = str(r.get(code_col, "")).zfill(6)
            sector = r.get("Sector") or r.get("Industry") or ""
            marcap = r.get("Marcap")
            cap_eok = round(marcap / 1e8) if pd.notna(marcap) and marcap else None
            info[code] = (sector, cap_eok)
    except Exception as e:
        print(f"[info] 업종·시총 매핑 수집 실패(무시): {e}")
    return info


def _fetch_range(bgn_de, end_de):
    """기간 내 상장사 공시를 (분류 가능한 것만) 수집. 3개월 이하 구간이어야 함."""
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
                if re.search(dc.RISK_PATTERNS, it["report_nm"]):
                    risk_event.add(code)
                sub = dc.classify(it["report_nm"])
                if sub is None:
                    continue
                rows.append({
                    "rcept_no": it["rcept_no"], "corp_code": it["corp_code"],
                    "date": it["rcept_dt"], "ticker": code, "nm": it["corp_name"],
                    "mk": market, "subtype": sub, "report_nm": it["report_nm"],
                })
            if page >= int(r.get("total_page", 1)):
                break
            page += 1
    return pd.DataFrame(rows), risk_event


def collect():
    today = datetime.today()
    week_bgn = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end_de = today.strftime("%Y%m%d")
    hist_bgn = (today - timedelta(days=REPEAT_WINDOW_DAYS)).strftime("%Y%m%d")
    print(f"이번 주: {week_bgn}~{end_de} / 연속판정 참조: {hist_bgn}~{end_de}")

    # 과거 3개월치를 한 번에 받아 그 안에서 이번 주와 연속 여부를 함께 계산
    df_all, risk_event = _fetch_range(hist_bgn, end_de)
    if df_all.empty:
        print("수집된 공시가 없습니다."); _save([]); return

    df_all = dc.refine(df_all, hist_bgn, end_de)

    # 위험 종목 제외
    risk = _risk_tickers() | risk_event
    df_all = df_all[~df_all.ticker.isin(risk)].copy()

    # 이번 주 대상만 분리
    df_all["cat"] = df_all["sub"].map(dc.category_of)
    week = df_all[df_all["date"] >= week_bgn].copy()
    print(f"이번 주 유효 공시 {len(week)}건 (위험 제외 종목 {len(risk)}개)")

    # 연속·중복 횟수: 같은 (ticker, cat)이 과거 3개월(이번 주 이전)에 몇 번 있었나
    past = df_all[df_all["date"] < week_bgn]
    repeat_count = past.groupby(["ticker", "cat"]).size().to_dict()

    # 업종·시총
    info = _sector_marcap()

    out = []
    for _, r in week.iterrows():
        code = r["ticker"]
        sector, cap = info.get(code, ("", None))
        out.append({
            "nm": r["nm"], "mk": r["mk"], "cat": r["cat"], "sub": r["sub"],
            "ticker": code, "corp_code": r["corp_code"], "date": r["date"],
            "ev": r["report_nm"],
            "scale": (f"시총 {cap:,}억" if cap else ""),
            "sector": sector or "",
            "repeat": int(repeat_count.get((code, r["cat"]), 0)),
            "dir": dc.DIR_DEFAULT.get(r["sub"], "n"),
            "link": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r['rcept_no']}",
        })
    _save(out)
    print(f"저장 완료: disclosures.json ({len(out)}건)")


def _save(items):
    payload = {"updated": datetime.today().strftime("%Y-%m-%d"), "rows": items}
    with open("disclosures.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    collect()
