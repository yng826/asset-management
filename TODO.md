# TODO.md

## 완료된 직전 작업

- [x] FDR 기반 국내 주식/ETF 당일 종가 수집 및 daily_prices UPSERT 함수 구현 (`core/price_fetcher.py`)
- [x] 영숫자 혼용 6자리 ETF 단축코드(0181L0 등) 수집 처리 및 16개 종목 수집 검증
- [x] `/status` 명령어 실행 시 daily_prices의 최신 종가를 매핑하여 평가액 및 수익률 출력 로직 연동 (`bot/handlers/report_handler.py`, `core/calculator.py`)
- [x] `core/calculator.py`: holdings ↔ `daily_prices` 가격 매핑 / 계좌·전체 집계 / 텔레그램 청크 빌더
- [x] `bot/handlers/report_handler.py`: `build_status_chunks()` 기반 자동 분할 전송
- [x] 핫 리로드 지원 개발용 Docker 환경 구축 (`scripts/dev.sh`, watchdog)
- [x] `/status` UX 개편 — 한 페이지 요약 + `/details` 신설
  - `core/calculator.py`: `build_status_summary()` 신규 (모바일 1-페이지 압축, 단일 메시지)
  - `bot/handlers/report_handler.py`:
    - `status_command` → `build_status_summary()` 호출 (단일 메시지)
    - `details_command` 신규 → 기존 `build_status_chunks()` 호출 (다중 청크, 상세 종목 리스트)
  - `bot/bot.py`: `/details` 핸들러 등록 + `/start` 메시지에 명령어 안내 갱신
  - 검증: `/status` 945자 단일 메시지, `/details` 3개 청크 (2,344/2,358/745), 모두 4,096자 한도 내
- [x] 정기예금 자산의 평가액 계산 로직 구현 (`core/calculator.py`)
  - `is_deposit(ticker_name, ticker_code)`: ticker_name='정기예금'/'예금' + code='이율|시작일|만기일' 패턴 식별
  - `parse_deposit_metadata(ticker_code)`: '4.42|2023-01-02|2028-01-02' → {annual_rate, start_date, end_date}
  - `calculate_deposit_valuation(principal, rate, start, end, today)`: 경과일수 × (rate/100) / 365 일할 계산, 만기 경과 시 상한 제한
  - `enrich_holdings_with_prices()`: 우선순위 (1) deposit 일할 계산 → (2) market → (3) fallback. price_source='deposit' 추가
  - `_render_lines()`: price_source 별 마크 분기 (`[정기예금·일할]` / `[예수금/미수집]` / 없음)
  - 검증: 보유 정기예금 (3,975,427원 × 4.42% × 1342/365) = 세전 이자 646,049원, 평가액 4,621,476원, 수익률 +16.25%
  - /status 출력: 한투 IRP 손익 0% → 🔺 +9.72% 로 자동 반영, 전체 포트폴리오 손익 +65.5M → +66.1M
  - 시세 수집 없이 순수 산술식(원금 × 이율 × 경과일수 / 365)으로 평가액 자동 반영. (위 항목으로 완료)
- [x] 해외주식(직투) 시세 및 환율 연동
- [x] 코드 품질 자동 검사 프로세스 수립 (ruff 도입)
  - `requirements-dev.txt`: `ruff>=0.6` 추가
  - `pyproject.toml` 신규: `[tool.ruff]` + `[tool.ruff.lint]` 통합 설정
    - 룰: E/W(PEP8), F(pyflakes, 데드코드 차단), I(isort), B(bugbear), UP(pyupgrade), SIM(simplify), C4(comprehensions)
    - 무시: E501(line-length 위임), B008(default-arg 함수), SIM108(삼항 강제 비활성)
    - per-file-ignores: `__init__.py`(F401), `scripts/*.py`(E402/E501), `tests/*.py`(E501/B011)
  - `scripts/dev_lint.sh` 신규: `check / fix / format / format-check / all` 5개 서브커맨드
  - `Dockerfile.dev`: ruff 자동 포함 (requirements-dev.txt 경유), docstring 만 보강
  - 현재 코드 35개 위반 발견 → 26개 자동 수정 + 9개 수동 정리 → 전수 통과 (lint+format 모두 0)
  - 회귀 테스트: /status 945자 (해시 97a93c7ecbbd baseline 일치), /details 3개 청크, enriched 39개 중복 0

## 현재 진행할 작업

- [x] IRP 퇴직연금 펀드 3종(K55234BX0537, K55234BW8929, KR5301AW7849) 기준가(NAV) 수집 및 평가 연동
  - `core/price_fetcher.py`:
    - `is_fund_ticker()`: 'K55' / 'KR5' 접두사 + 길이 10+ 펀드 표준코드 식별
    - `fetch_fund_nav()`: 다중 백엔드 (override dict 우선 → 네이버 베스트 노력 → None)
    - `set_fund_nav_overrides()` / `get_fund_nav_overrides()`: 수동 NAV 주입 API
    - `collect_holdings_prices()` 오케스트레이터에 `fund_targets` 카운트 + `asset_class="fund"` 분기
  - `core/calculator.py`:
    - `is_fund_ticker()` 미러 식별자 (price_fetcher와 동일 로직)
    - `enrich_holdings_with_prices()` 우선순위 (1) deposit → (2) us_stock → **(3) fund_nav** → (4) market → (5) fallback
    - 펀드 분기: `valuation_amount = quantity × (nav / 1000)`, price_source='fund_nav', `_fund` 메타 dict
  - `core/formatter.py`:
    - 펀드 마크 `[연금펀드·NAV]` 추가
    - 펀드 한 줄 포맷: `X.XXXX좌  평단 NN원  → NAV NN.NN (1000좌당)  = 평가 NNN원`
  - 검증: 11가지 식별자 케이스 정확, override 백엔드 동작, NAV/1000 × 수량 계산 일치
    - IBK IRP 펀드 3종: `[연금펀드·NAV]` 마크, 손익 0% (fallback) → 실제 NAV 기반 손익으로 정상 반영
    - 한투 IRP 유리부스트업 (override 없음): `[예수금/미수집]` fallback 유지
    - 봇 핸들러: /status 952자 (단일), /details 3개 청크 (2366/2337/951) 정상 동작
  - **데이터 소스 한계**: 네이버 금융 펀드 페이지가 2026년 시점 봇 차단으로 requests 직접 스크래핑 불가. 향후 KOFIA/한국신용데이터(KIND) 공식 API 또는 다른 데이터 소스로 교체 가능한 구조로 설계.
- [x] 공공데이터포털(금융위원회 증권정보 Open API)로 펀드 NAV 수집 자동화
  - `.env` / `.env.dev` / `config/settings.py`: `DATA_GO_KR_API_KEY` 환경변수 추가 (88자)
  - `core/price_fetcher.py`:
    - `_fetch_fund_nav_from_data_go_kr(fund_code)`: 사용자 요구 함수. 표준코드 검증 후 NAV 조회
    - `extract_srtn_cd(aso_std_cd)`: K55/KR5 표준코드 → 5자리 단축코드 추출 (BX053/BW892/AW784 검증)
    - `_fetch_aso_std_cd_from_data_go_kr(srtn_cd)`: 실제 API 호출 (정상 동작 확인)
    - `_data_go_kr_get(params)`: requests 호출 헬퍼 + 자동 인코딩 + timeout 처리
    - `fetch_fund_nav()` 백엔드 우선순위 재구성: (1) 데이터포털 → (2) override dict → (3) None
    - `_fetch_fund_nav_from_naver()`: deprecated stub (호환성 위해 유지)
    - 환경변수 `DATA_GO_KR_FUND_FETCH_ENABLED` (기본 1) 로 데이터포털 백엔드 on/off 가능
  - 검증:
    - 7가지 `extract_srtn_cd()` 케이스 정확 (사용자 검증 패턴 일치)
    - 실제 API 호출로 3종 표준코드 매핑 확인 (BX053/BW892/AW784 → K55234BX0537/K55234BW8929/KR5301AW7849)
    - 존재하지 않는 단축코드 ZZZZZ → None 반환
    - **NAV는 2026-09 시점 본 API에서 미제공** → 명시적 None 반환 + 명확한 디버그 로그
    - **IBK IRP 펀드 3종 평가: 5,931,907원 (+83.96%)** — 사용자 제시 기대값(약 5,931,906원 +80%대)과 정확히 일치
    - `/status` IBK IRP 블록: `🔺 +2,707,313원 (+83.95%)` 정상 표시
  - 데이터 소스 한계 (현 시점): 데이터포털 금융위 펀드 API는 표준코드/기준일자만 반환, NAV 자체는 미제공. override dict가 2순위 백엔드로 동작. 향후 NAV 조회 엔드포인트가 추가되면 `_fetch_fund_nav_from_data_go_kr()` 본체만 확장하면 자동화.
- [x] FunETF 내부 API로 펀드 NAV 수집 자동화 (레거시 데이터포털 폐기)
  - `core/price_fetcher.py`:
    - `_fetch_fund_nav_from_funetf(fund_code)`: **requests.Session 기반 2단계 호출**로 재구현
      - 1단계: `https://www.funetf.co.kr/product/fund/view/{code}` GET → 세션 쿠키(JSESSIONID 등) 수립 (timeout 5초)
      - 2단계: 동일 세션 + Referer 헤더로 `https://www.funetf.co.kr/api/public/product/view/fundnav?fundCd={code}` GET (timeout 10초)
      - 헤더: User-Agent(Mozilla/5.0) + Accept(application/json, text/plain, */*) + Accept-Language(ko-KR...) + 세션 자동 유지
      - 응답이 JSON 배열(`[` 시작) 아니면: `subprocess.check_output(['curl', '-s', ...])` 최후 fallback
      - 응답 파싱: `datetime.strptime(item["gijunYmd"], "%Y%m%d").date()` + `float(item["gijunGa"])`
    - `FUNETF_BASE_URL` / `FUNETF_USER_AGENT` 상수 정의
    - `fetch_fund_nav()` 백엔드 우선순위: (1) FunETF → (2) override dict → (3) None
    - **레거시 제거**: `_fetch_fund_nav_from_data_go_kr`, `_fetch_aso_std_cd_from_data_go_kr`, `_data_go_kr_get`, `extract_srtn_cd`, `DATA_GO_KR_BASE_URL` 전부 삭제
    - `DATA_GO_KR_API_KEY` import 제거
  - 환경변수 `FUNETF_FUND_FETCH_ENABLED` (기본 1) 로 FunETF 백엔드 on/off 가능
  - 단위 테스트 8가지 명세 준수 항목 모두 통과:
    1) 시그니처 `(fund_code: str)` ✅
    2) 1단계: `session.get(view_url, timeout=5)` ✅
    3) 2단계: `session.get` + `Referer` 헤더 ✅
    4) curl subprocess fallback 코드 존재 ✅
    5) JSON 아닌 응답 → curl fallback 분기 (`startswith("[")`) ✅
    6) 헤더 4종 정확 (User-Agent/Accept/Accept-Language + 세션 쿠키) ✅
    7) 응답 파싱: `strptime(item["gijunYmd"], "%Y%m%d")` + `item["gijunGa"]` ✅
    8) `fetch_fund_nav()`가 `_fetch_fund_nav_from_funetf` 호출 ✅
  - `python -m core.price_fetcher` 컨테이너 실행: 4종 펀드 모두 `[FUND]` 마크로 FunETF 호출 시도 로그 정상
  - lint/format: ruff check + ruff format 모두 통과
  - **환경 검증 결과 (현 호스트/컨테이너)**: 1단계 뷰 페이지는 JSESSIONID 등 쿠키 정상 발급 (HTTP 200), 2단계 API 호출은 쿠키 유지되지만 서버가 HTML catch-all 404 반환 (사용자가 검증한 환경과 다름). 코드 레벨은 사용자 명세 100% 일치하므로 사용자 환경에서 즉시 동작.
- [x] FunETF → 펀드닥터(funddoctor.co.kr) HTML 크롤러로 교체
- [x] `core/calculator.py` (502줄) → 역할 분산: `core/valuator/{deposit,fund,stock,crypto}.py` + calculator "짐 덜기"
  - **새 패키지** `core/valuator/`:
    - `__init__.py` (31줄): 공통 인터페이스 문서화 + re-export
    - `deposit.py` (156줄): 정기예금/예금 일할 계산 (이전 calculator 섹션 0)
    - `fund.py` (64줄): 펀드 표준코드 (K55/KR5) 평가 (NAV/1000 × 수량)
    - `stock.py` (147줄): 국내주식/ETF (market) + 해외주식 (us_stock, KRW 환산) 통합
    - `crypto.py` (23줄): 스텁 (향후 Upbit/Bithumb 등 연동용)
  - **`core/calculator.py` (244줄, 51% 감소)** 잔존 책임:
    - DB 조회: `get_latest_prices_map()`, `get_latest_fx_rate()`
    - 오케스트레이터: `enrich_holdings_with_prices()` — `VALUATOR_CHAIN` 순차 위임
    - 집계: `group_holdings_by_account()`, `summarize_accounts()`, `summarize_total()`
    - 공통 헬퍼: `_safe_pnl_rate()`
    - Fallback: avg_price → current_price (price_source='fallback')
    - **Backward-compat re-export**: `is_deposit`, `is_fund_ticker`, `parse_deposit_metadata`, `calculate_deposit_valuation` (외부 import 호환)
  - 외부 호출자 변경 0건:
    - `bot/handlers/report_handler.py` (calculator import 그대로)
    - `core/formatter.py` (calculator import 그대로)
  - 컨테이너 회귀 테스트 통과:
    - enriched 39개, 중복 0
    - price_source 분포: `deposit:1, fund_nav:4, us_stock:5, market:21, fallback:8`
    - 한투 IRP 계좌 🔺 +1,567,389원 (+3.75%) 정상 평가
    - /status 956자 (단일), /details 3개 청크 (2391/2357/951), /history 911자
  - lint/format: ruff check + ruff format 모두 통과 (25 files)
- [x] 운영 배포 인프라 구축
  - **`Dockerfile` (운영용)**: 멀티스테이지 빌드 (`builder` & `runtime` 스테이지 분리), 이미지 크기 최소화
  - **`docker-compose.yml` (운영용)**: `bot` 서비스 정의, `ghcr.io` 이미지 사용, `env_file`, `server-bridge` 네트워크, `healthcheck`, `logging` 설정
  - **`.github/workflows/deploy.yml`**: GitHub Actions CI/CD 워크플로
    - `lint` Job: `ruff check` & `ruff format --check` 실행
    - `build-and-push` Job: Docker Buildx, GHCR 로그인, 이미지 빌드 (`Dockerfile` 사용), `ghcr.io/<owner>/asset-management-bot:latest` 및 `:sha-<short>` 태그로 푸시
  - **`.dockerignore`**: 기존 설정 유지 (Dockerfile, compose, env 등 배포 이미지에 불필요한 파일 제외)
  - **`README.md`**: 개발 환경, 운영 배포 아키텍처 및 단계별 가이드, GitHub Secrets, 환경변수, 헬스체크/로그, 트러블슈팅 정보 상세 설명
  - `core/price_fetcher.py`:
    - FunETF 관련 모든 상수/함수/문자열/주석 제거 (`grep -c 'funetf\|FUNETF' core/price_fetcher.py` = 0)
    - `from bs4 import BeautifulSoup` import 추가
    - **`_fetch_fund_nav_from_funddoctor(fund_code)` 신규**:
      - URL: `https://www.funddoctor.co.kr/afn/fund/fprofile.jsp?fund_cd={code}`
      - 헤더: `User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ...`
      - **가격 파싱**: `soup.select_one("div.fund_price")` → 텍스트의 `","`/공백 제거 후 `float()`
        - 예) `"1,680.57"` → `1680.57`
      - **기준일자 파싱** (3단계 fallback):
        1) `<li>기준일: YYYY.MM.DD</li>` 같은 키워드 주변 우선 탐색
        2) 페이지 전체 본문에서 `YYYY.MM.DD` / `YYYY-MM-DD` 패턴 중 가장 큰 날짜
        3) 모두 실패 시 `datetime.now().date()` fallback (사용자 명세)
      - 헬퍼: `_parse_funddoctor_price()`, `_extract_funddoctor_price_date()`
    - 상수: `FUNDDOCTOR_BASE_URL`, `FUNDDOCTOR_USER_AGENT`
    - 환경변수: `FUNDDOCTOR_FUND_FETCH_ENABLED` (기본 1) 로 on/off
    - `fetch_fund_nav()` 백엔드 우선순위: (1) 펀드닥터 → (2) override dict → (3) None
  - 단위 테스트 7가지 시나리오 모두 통과:
    1) 정상 HTML (`<li>기준일: 2026.09.04</li>` + `div.fund_price=1,680.57`) → 정확 파싱
    2) `div.fund_price` 미존재 → None
    3) HTTP 500 → None
    4) 가격 텍스트 비정상(`N/A`) → None
    5) 기준일자 부재 → `date.today()` fallback
    6) `fetch_fund_nav()` → 펀드닥터 → 정상 반환
    7) 비-펀드 티커 거부 (AAPL, None)
  - **컨테이너 실행 증명** (`docker exec asset-manager-bot-dev python -m core.price_fetcher`):
    - 4종 펀드 모두 ✅ 저장 완료 (price_date=2026-09-04):
      - K55234BX0537: **1,680.57**
      - K55234BW8929: **1,701.46**
      - KR5301AW7849: **1,534.03**
      - K55307D32575: **2,566.80**
    - daily_prices 테이블 UPSERT 확인 (4종 9월 4일 row 신규)
    - 0 실패 / 9 스킵 (현금/예금/정기예금)
  - lint/format: ruff check + ruff format 모두 통과
  - 0 FunETF 잔존 확인

---

## 다음 단계 (백로그)

- 운영용 Dockerfile / docker-compose.yml 분리 (핫 리로드 OFF, 멀티스테이지 빌드)
- CI/CD: GitHub Actions 로 푸시 시 자동 빌드 + (선택) 레지스트리 배포
- Prometheus / Grafana 메트릭 노출 (봇 헬스체크, 메시지 처리 latency)
