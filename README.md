# 주간 공시 분석 대시보드 (하이브리드)

DART 공시를 주 단위로 수집·분류하고, 위험 종목(관리종목 등)을 걸러낸 뒤,
공시 유형별 "과거 주가 반응" 통계와 함께 웹 대시보드로 보여줍니다.

## 구조

```
dart-weekly/
├─ index.html               ← 대시보드 (GitHub Pages가 서빙, 두 JSON을 fetch)
├─ disclosures.json         ← 그 주의 공시 (매주 Actions가 갱신)
├─ event_study_hist.json    ← 유형별 과거 주가 반응 통계 (가끔 로컬에서 갱신)
├─ dart_common.py           ← 공통 로직 (분류·카테고리·세부방향 분리)
├─ collect_disclosures.py   ← 주간 수집 (DART 전용) → disclosures.json
├─ event_study.py           ← 백테스트 (pykrx) → event_study_hist.json
├─ requirements.txt         ← Actions용 의존성
└─ .github/workflows/weekly.yml  ← 매주 자동 실행
```

## 역할 분담 (하이브리드)

| 작업 | 어디서 | 무엇을 쓰나 | 주기 |
|---|---|---|---|
| 주간 공시 수집 | GitHub Actions (클라우드) | DART API only | 매주 자동 |
| 백테스트 통계 | 내 PC (로컬) | pykrx | 가끔 (월/분기) |
| 대시보드 열람 | GitHub Pages | — | 상시(URL) |

pykrx는 클라우드 IP에서 막힐 수 있어 백테스트만 로컬에서 돌립니다.

## 설치 순서

1. 이 폴더를 GitHub 저장소로 push
2. 저장소 **Settings → Secrets and variables → Actions** 에서
   `DART_API_KEY` 를 추가 (https://opendart.fss.or.kr 에서 무료 발급)
3. 저장소 **Settings → Pages** 에서 Source를 `main` 브랜치로 지정 → 사이트 URL 생성
4. **Actions** 탭에서 `weekly-disclosures` 워크플로를 한 번 수동 실행(Run workflow)

## 백테스트 통계 갱신 (로컬)

```bash
pip install requests pandas pykrx
export DART_API_KEY=발급받은키          # Windows: set DART_API_KEY=...
python event_study.py                   # event_study_hist.json 생성
git add event_study_hist.json && git commit -m "update backtest" && git push
```

## 로컬에서 화면만 미리 보기

```bash
python -m http.server 8000
# http://localhost:8000 접속  (파일 더블클릭 X — fetch가 막힙니다)
```

## 주의

- 통계는 과거 경향이며 미래 수익을 보장하지 않습니다. 투자 자문이 아닙니다.
- 게시되는 JSON/HTML은 공개됩니다. **API 키는 절대 파일에 넣지 말 것** (Secrets/환경변수만 사용).
- 거래정지·불성실공시 종목은 KRX 정보데이터시스템에서 받아 `risk_extra.json`
  (종목코드 배열)로 저장해두면 제외 필터에 자동 합쳐집니다.
