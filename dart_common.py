"""
dart_common.py — 수집 스크립트와 백테스트 스크립트가 공유하는 공통 로직.

여기에 분류 규칙·카테고리·방향 기본값·상세조회(세부 방향 분리)를 모아두어,
collect_disclosures.py 와 event_study.py 가 '똑같은 유형 이름'을 쓰도록 맞춥니다.
(유형 이름이 일치해야 대시보드가 공시 ↔ 통계를 join 할 수 있습니다.)
"""
import os
import re
import time
import requests

API_KEY = os.environ.get("DART_API_KEY", "여기에_발급받은_인증키_입력")
BASE = "https://opendart.fss.or.kr/api"

# ── 1차 분류 규칙 (보고서명 기반) ─────────────────────────────────────────
SUBTYPE_RULES = [
    ("자기주식 소각",          r"자기주식.*소각"),
    ("자기주식 취득",          r"자기주식취득|자기주식.*취득"),
    ("현금배당",               r"현금.?현물배당|현금배당|배당결정"),
    ("무상증자",               r"무상증자"),
    ("잠정실적",               r"영업.?잠정.?실적|잠정실적"),
    ("손익구조 변동",          r"손익구조.*변동|매출액.*변동"),
    ("유상증자",               r"유상증자"),
    ("전환사채(CB)",           r"전환사채권?발행|전환사채"),
    ("신주인수권부사채(BW)",   r"신주인수권부사채"),
    ("단일판매·공급계약",      r"단일판매|공급계약"),
    ("시설투자·증설",          r"신규시설투자|유형자산.*취득|시설투자"),
    ("타법인 취득·M&A",        r"타법인.*주식.*취득|영업양수"),
    ("기술이전·라이선스",      r"기술이전|라이선스|기술도입"),
    ("합병",                   r"합병"),
    ("분할",                   r"분할결정|회사분할|물적분할|인적분할"),
    ("최대주주 변경",          r"최대주주.*변경"),
    ("5% 대량보유 변동",       r"대량보유상황"),
    ("임원·주요주주 소유변동",  r"특정증권등소유상황"),
]

# 위험 이벤트(제외 대상)로 보는 보고서명 패턴 — 해당 종목은 리포트에서 제외
RISK_PATTERNS = r"(감사의견|의견거절|한정의견|자본잠식|횡령|배임|부도발생|영업정지|회생절차|파산|상장폐지)"

# 세부 유형 → 대분류(카테고리)
CATEGORY = {
    "자기주식 취득": "주주환원", "자기주식 소각": "주주환원",
    "현금배당": "주주환원", "무상증자": "주주환원",
    "잠정실적": "실적·펀더멘털", "손익구조 변동": "실적·펀더멘털",
    "단일판매·공급계약": "성장·투자", "시설투자·증설": "성장·투자",
    "타법인 취득·M&A": "성장·투자", "기술이전·라이선스": "성장·투자",
    "합병": "지배구조·구조개편", "분할": "지배구조·구조개편", "최대주주 변경": "지배구조·구조개편",
    "유상증자": "자금조달", "전환사채(CB)": "자금조달", "신주인수권부사채(BW)": "자금조달",
    "5% 대량보유 변동": "지분변동", "임원·주요주주 소유변동": "지분변동",
    "사후공시(발행결과)": "기타", "자회사 공시": "기타",
}

# 세부 유형 → 화면 표시용 방향 태그 기본값 (u호재 / d악재 / c주의 / n중립)
# 실제 방향성 판단은 event_study 통계(hist)로 보강됩니다. 이건 첫 표시용 힌트.
DIR_DEFAULT = {
    "자기주식 취득": "u", "자기주식 소각": "u", "현금배당": "n", "무상증자": "u",
    "잠정실적": "n", "손익구조 변동": "n",
    "단일판매·공급계약": "u", "시설투자·증설": "n", "타법인 취득·M&A": "c", "기술이전·라이선스": "u",
    "합병": "c", "분할": "d", "최대주주 변경": "c",
    "유상증자(주주배정)": "d", "유상증자(제3자배정)": "c", "유상증자(일반공모)": "d", "유상증자(기타)": "c",
    "전환사채(CB)": "d", "신주인수권부사채(BW)": "d",
    "5% 대량보유 변동": "u", "임원·주요주주 매수": "u", "임원·주요주주 매도": "d",
    "임원·주요주주 소유변동": "n",
    "사후공시(발행결과)": "n", "자회사 공시": "n",
}

# 제외/분리 규칙 (분류보다 먼저 적용)
CORRECTION_PAT = r"\[(기재|첨부|권리|첨부서류|정정).*?정정.*?\]|\[정정\]"   # [기재정정] 등 정정공시
POSTHOC_PAT    = r"발행결과|발행실적|실적보고|증권발행실적"                    # 사후 보고
SUBSIDIARY_PAT = r"종속회사|자회사"                                            # 자회사 소식

def classify(report_nm: str):
    """보고서명 → 세부 유형. 정정공시는 제외(None), 자회사·사후공시는 별도 유형."""
    # 1) 정정공시 제외 — 같은 건이 중복 계상되는 걸 막음 (원본만 사용)
    if re.search(CORRECTION_PAT, report_nm):
        return None
    # 2) 사후 공시(발행결과·실적보고) 별도 분리 — 이미 알려진 정보
    if re.search(POSTHOC_PAT, report_nm):
        return "사후공시(발행결과)"
    # 3) 자회사·종속회사 공시 별도 분리 — 본사 이벤트가 아님
    if re.search(SUBSIDIARY_PAT, report_nm):
        return "자회사 공시"
    # 4) 본래 유형 분류
    for name, pat in SUBTYPE_RULES:
        if re.search(pat, report_nm):
            return name
    return None

def category_of(sub: str):
    base = sub.split("(")[0] if sub.startswith("유상증자") else sub
    if sub.startswith("임원·주요주주"):
        base = "임원·주요주주 소유변동"
    return CATEGORY.get(base, "기타")

def get(path, retries=3, **params):
    params["crtfc_key"] = API_KEY
    for attempt in range(retries):
        try:
            return requests.get(f"{BASE}/{path}", params=params, timeout=30).json()
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))  # 1.5s, 3s … 점증 대기 후 재시도
                continue
            return {"status": "ERR", "message": str(e)}

# ── 세부 방향 분리 (DART 상세 API — 클라우드에서도 안전) ──────────────────
def _to_int(v):
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except Exception:
        return 0

def map_rights(ic_mthn: str):
    s = ic_mthn or ""
    if "제3자" in s or "3자" in s:  return "유상증자(제3자배정)"
    if "주주배정" in s:            return "유상증자(주주배정)"
    if "공모" in s:                return "유상증자(일반공모)"
    return "유상증자(기타)"

def refine(df, bgn_de, end_de):
    """유상증자 → 배정방식, 임원·주요주주 → 매수/매도 로 세분화한 'sub' 컬럼 생성."""
    df = df.copy()
    if df.empty or "subtype" not in df.columns:
        df["sub"] = []
        return df
    df["sub"] = df["subtype"]

    groups = list(df[df.subtype == "유상증자"].groupby("corp_code"))
    for i, (corp_code, g) in enumerate(groups, 1):
        r = get("piicDecsn.json", corp_code=corp_code, bgn_de=bgn_de, end_de=end_de)
        book = {row.get("rcept_no"): row.get("ic_mthn", "")
                for row in r.get("list", [])} if r.get("status") == "000" else {}
        for idx, rec in g.iterrows():
            df.at[idx, "sub"] = map_rights(book.get(rec["rcept_no"], ""))
        if i % 20 == 0 or i == len(groups):
            print(f"    유상증자 배정방식 확인 {i}/{len(groups)}개사")
        time.sleep(0.2)

    groups = list(df[df.subtype == "임원·주요주주 소유변동"].groupby("corp_code"))
    for i, (corp_code, g) in enumerate(groups, 1):
        r = get("elestock.json", corp_code=corp_code)
        net = {}
        if r.get("status") == "000":
            for row in r.get("list", []):
                rn = row.get("rcept_no")
                net[rn] = net.get(rn, 0) + _to_int(row.get("sp_stock_lmp_irds_cnt"))
        for idx, rec in g.iterrows():
            d = net.get(rec["rcept_no"])
            df.at[idx, "sub"] = ("임원·주요주주 매수" if (d or 0) > 0
                                 else "임원·주요주주 매도" if (d or 0) < 0
                                 else "임원·주요주주 소유변동")
        if i % 20 == 0 or i == len(groups):
            print(f"    임원 매수/매도 확인 {i}/{len(groups)}개사")
        time.sleep(0.2)
    return df
