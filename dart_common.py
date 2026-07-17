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
    "유상증자": "d",
    "전환사채(CB)": "d", "신주인수권부사채(BW)": "d",
    "5% 대량보유 변동": "n",
    "임원·주요주주 소유변동": "n",
    "사후공시(발행결과)": "n", "자회사 공시": "n",
}

# 제외/분리 규칙 (분류보다 먼저 적용)
CORRECTION_PAT = r"^\["                    # [기재정정]/[첨부추가] 등 — 원본의 수정본이므로 제외
POSTHOC_PAT    = r"발행결과|발행실적|실적보고|증권발행실적"      # 사후 보고
SUBSIDIARY_PAT = r"종속회사|자회사"                            # 자회사 소식
# 결정 이후의 절차·안내 공시 — 이벤트 스터디 대상이 아니므로 제외
PROCEDURAL_PAT = (r"청약결과|발행가액|권리락|안내공시|특수관계인.*참여|"
                  r"철회|취소|실권주.*청약|배정결과|납입완료|상장예정")

def classify(report_nm: str):
    """보고서명 → 세부 유형. 정정공시는 제외(None), 자회사·사후공시는 별도 유형."""
    # 1) 정정·첨부본 제외 — 같은 건이 중복 계상되는 걸 막음 (원본만 사용)
    if re.search(CORRECTION_PAT, report_nm):
        return None
    # 1-2) 결정 이후 절차·안내 공시 제외 (청약결과·발행가액·권리락 등)
    if re.search(PROCEDURAL_PAT, report_nm):
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
    """세부 유형 컬럼 'sub' 생성.

    과거에는 유상증자 배정방식(piicDecsn)과 임원 매수/매도(elestock)를 상세 API로
    세분화했으나, 두 API 모두 해당 공시의 '일부만' 보유하고 있어 매칭에 실패한 건이
    많았습니다. 판별된 건만 뽑으면 선택 편향이 생기므로 세분화를 하지 않습니다.
    (배정방식·매수/매도는 대시보드의 DART 원문 링크로 개별 확인하세요.)
    """
    df = df.copy()
    if df.empty or "subtype" not in df.columns:
        df["sub"] = []
        return df
    df["sub"] = df["subtype"]
    return df

# ── 업종 매핑 (FinanceDataReader KRX-DESC 의 Industry = 표준산업분류) ──────
# 원본 업종명이 "측정, 시험, 항해, 제어 및 기타 정밀기기 제조업; 광학기기 제외" 처럼
# 길어서 화면에 쓰기 어렵습니다. 아래 키워드로 짧은 그룹명에 묶습니다.
INDUSTRY_GROUPS = [
    ("임대·리스",     r"임대업|리스업"),          # '기계 및 장비 임대업' 이 기계로 잡히지 않도록 먼저
    ("반도체",        r"반도체"),
    ("전자부품",      r"전자부품|인쇄회로|축전지|전지 제조"),
    ("디스플레이",    r"표시장치|디스플레이"),
    ("통신·방송장비", r"통신 및 방송 장비|통신장비"),
    ("컴퓨터·주변기기", r"컴퓨터 및 주변장치"),
    ("IT·소프트웨어", r"소프트웨어|컴퓨터 프로그래밍|정보서비스|정보 서비스|자료처리|포털"),
    ("통신서비스",    r"전기통신업"),
    ("제약·바이오",   r"의약품|의약물질|의약 관련|의료용품|의료용 물질|생물학적 제제"),
    ("의료기기",      r"의료용 기기|의료기기"),
    ("정밀·광학기기", r"정밀기기|광학기기|측정, 시험"),
    ("자동차·부품",   r"자동차"),
    ("조선·기타운송", r"선박|철도|항공기 제조"),
    ("기계·장비",     r"기계 및 장비|기계 제조"),
    ("전기장비",      r"전기장비|전동기|발전기|절연선|케이블 제조|가정용 기기"),
    ("화학",          r"화학물질|화학제품|플라스틱|고무"),
    ("석유·정유",     r"석유|코크스"),
    ("철강·금속",     r"1차 금속|1차 비철금속|비철금속|금속 가공|철강"),
    ("비금속광물",    r"비금속 광물"),
    ("종이·목재",     r"펄프|종이|목재|판지"),
    ("가구",          r"가구"),
    ("생활용품",      r"생활용품|위생용품|세제|화장품"),
    ("섬유·의류",     r"섬유|의복|가죽|신발|직물|편조|봉제|방적|염색"),
    ("식음료",        r"식료품|식품|음료|주류|제과|육류|수산물|곡물|과실|채소|낙농|사료|커피|차 가공"),
    ("담배",          r"담배"),
    ("건설",          r"건설업|건물 건설|토목|전문직별 공사"),
    ("부동산",        r"부동산"),
    ("유통·도소매",   r"도매|소매|상품 중개"),
    ("운송·물류",     r"운송|운수|창고|항공 여객|해상"),
    ("숙박·음식",     r"숙박|음식점"),
    ("미디어·콘텐츠", r"출판|영상|방송|오디오물|게임"),
    ("엔터·레저",     r"예술|스포츠|여가|오락"),
    ("금융",          r"금융업|금융 지원|은행|신용|투자|보험|연금"),
    ("지주회사",      r"지주회사|회사 본부"),
    ("전문서비스",    r"전문, 과학|연구개발|법무|회계|광고|시장조사|건축기술|엔지니어링"),
    ("사업지원",      r"사업시설|사업 지원|고용알선|경비"),
    ("전기·가스·수도", r"전기, 가스|^전기업|수도|증기|가스 제조"),
    ("환경·재활용",   r"폐기물|환경 정화|재활용"),
    ("농림어업",      r"농업|임업|어업|축산"),
    ("광업",          r"광업"),
    ("기타 제조",     r"그외 기타 제품|기타 제품 제조"),
    ("교육",          r"교육|교습|학원"),
    ("의료·복지",     r"보건업|사회복지"),
]


def short_industry(name: str) -> str:
    """긴 표준산업분류명 → 짧은 그룹명. 못 찾으면 앞부분만 잘라서 반환."""
    if not name or (isinstance(name, float)):
        return ""
    s = str(name).strip()
    if not s or s.lower() == "nan":
        return ""
    for label, pat in INDUSTRY_GROUPS:
        if re.search(pat, s):
            return label
    head = s.split(";")[0].split(",")[0].strip()
    return head[:14] if head else ""


def sector_map():
    """종목코드 → 짧은 업종명. FDR KRX-DESC 의 Industry 사용."""
    try:
        import FinanceDataReader as fdr
        d = fdr.StockListing("KRX-DESC")
        code_col = "Code" if "Code" in d.columns else "Symbol"
        if "Industry" not in d.columns:
            return {}
        return {str(r[code_col]).zfill(6): short_industry(r["Industry"])
                for _, r in d.iterrows()}
    except Exception as e:
        print(f"[업종] 목록 수집 실패(무시): {e}")
        return {}
