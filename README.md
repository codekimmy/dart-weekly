# 주간 공시 분석 대시보드

DART 공시를 수집·분류하고, 위험 종목을 거른 뒤, 과거 주가 반응 통계·거래량 급증·추세/재무
필터와 함께 웹 대시보드로 보여줍니다.

## 화면 (index.html — GitHub Pages)
- **공시 리포트**: 이번 주 공시를 6개 대분류/18개 세부 유형으로 분류. DART 원문 링크, 업종,
  시총, 연속 공시 배지, 과거 주가 반응(T+1/5/20·상승확률), 실제 반응 비교
- **거래량 급증**: 20일 평균 대비 3배+ & 상승 종목 (공시 있음 배지로 교차 확인)
- **추세·모멘텀**: 200일선 위 + 52주 위치 + 상대강도, 재무 양호/주의 배지

## 파일 역할

| 파일 | 실행 위치 | 역할 | 산출물 |
|---|---|---|---|
| `collect_disclosures.py` | Actions(주간) | 공시 수집·분류·위험제외·링크/업종/시총/연속 | `disclosures.json` |
| `event_study.py` | 로컬 | 이벤트 스터디 백테스트 (연도별) | `event_study_hist.json` |
| `volume_scan.py` | 로컬 | 거래량 급증 스캔 | `volume_spikes.json` |
| `trend_scan.py` | 로컬 | 추세·모멘텀 스캔 | `trend_signals.json` |
| `health_scan.py` | 로컬 | 재무 건전성 점검 | `health_signals.json` |
| `track_prices.py` | 로컬 | 공시 후 실제 주가 채우기 | `disclosures.json` 갱신 |
| `dart_common.py` | 공용 | 분류 규칙·카테고리·세부방향 분리 | — |

## 백테스트 사용법 (연도별 분할)

```bash
set DART_API_KEY=발급받은키          # Windows

python event_study.py 2024           # 2024년만 처리 → _year_2024.csv
python event_study.py 2023           # 2023 추가 (2024는 재수집 없이 합산)
python event_study.py 2022 2023 2024 # 여러 해 차례로
python event_study.py                # 수집 없이 기존 연도 조각만 합쳐 통계 재생성
```

- 중간에 끊겨도 같은 명령을 다시 실행하면 이어서 진행합니다.
- 이미 끝난 연도는 자동으로 건너뜁니다.
- 표본 20건 미만 유형은 `(표본부족)`으로 표시됩니다.

## 로컬 스캔

```bash
python volume_scan.py        # 최근 거래일 기준
python trend_scan.py
python health_scan.py        # DART_API_KEY 필요
python track_prices.py       # 공시 며칠 뒤 실행
```

## 로컬에서 화면 미리보기

```bash
python -m http.server 8000
# http://localhost:8000  (파일 더블클릭 X — fetch가 막힘)
```

## 설치

```bash
pip install requests pandas pykrx finance-datareader
```

## 주의

- 통계는 과거 경향이며 미래 수익을 보장하지 않습니다. **투자 자문이 아닙니다.**
- 거래량 급증·추세 지표는 후보를 좁히는 도구이지 매수 신호가 아닙니다.
- 게시되는 JSON/HTML은 공개됩니다. **API 키는 파일에 넣지 말고** Secrets/환경변수만 사용하세요.
- `_year_*.csv` 등 로컬 캐시는 `.gitignore`로 제외됩니다.
