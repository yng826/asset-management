# 작업 완료 아카이브 (Historical Archive)

## 초기 구축 및 핵심 기능

- [x] FDR 기반 국내 주식/ETF 당일 종가 수집 및 daily_prices UPSERT 함수 구현 (`core/price_fetcher.py`)[cite: 1]
- [x] 영숫자 혼용 6자리 ETF 단축코드(0181L0 등) 수집 처리 및 16개 종목 수집 검증[cite: 1]
- [x] `/status` 명령어 실행 시 daily_prices의 최신 종가를 매핑하여 평가액 및 수익률 출력 로직 연동 (`bot/handlers/report_handler.py`, `core/calculator.py`)[cite: 1]
- [x] `core/calculator.py`: holdings ↔ `daily_prices` 가격 매핑 / 계좌·전체 집계 / 텔레그램 청크 빌더[cite: 1]
- [x] `bot/handlers/report_handler.py`: `build_status_chunks()` 기반 자동 분할 전송[cite: 1]
- [x] 핫 리로드 지원 개발용 Docker 환경 구축 (`scripts/dev.sh`, watchdog)[cite: 1]
- [x] `/status` UX 개편 — 한 페이지 요약 + `/details` 신설[cite: 1]
  - `core/calculator.py`: `build_status_summary()` 신규 (모바일 1-페이지 압축, 단일 메시지)[cite: 1]
  - `bot/handlers/report_handler.py`: `status_command` 단일 메시지 호출 / `details_command` 상세 다중 청크 호출[cite: 1]
  - `bot/bot.py`: `/details` 핸들러 등록 및 안내 갱신[cite: 1]
- [x] 정기예금 자산의 평가액 계산 로직 구현 (`core/calculator.py`)[cite: 1]
  - `is_deposit`, `parse_deposit_metadata`, `calculate_deposit_valuation` 일할 계산식 적용[cite: 1]
  - 시세 수집 없이 순수 산술식(원금 × 이율 × 경과일수 / 365)으로 평가액 자동 반영[cite: 1]
- [x] 해외주식(직투) 시세 및 환율 연동[cite: 1]
- [x] 코드 품질 자동 검사 프로세스 수립 (`ruff` 도입, `pyproject.toml`, `scripts/dev_lint.sh`)[cite: 1]

## 펀드 NAV 수집 연동 변천사

- [x] IRP 퇴직연금 펀드 기준가(NAV) 수집 및 평가 연동 (1차: 네이버 금융/오버라이드 방식)[cite: 1]
- [x] 공공데이터포털(금융위원회 증권정보 Open API) 연동 시도 (표준코드 매핑 확인, NAV 데이터 미제공 한계로 전환)[cite: 1]
- [x] FunETF 내부 세션 기반 API 연동 시도 (환경별 차단 이슈로 전환)[cite: 1]
- [x] 펀드닥터(funddoctor.co.kr) HTML 크롤러로 최종 안착[cite: 1]
  - 4종 펀드 자동 수집 및 daily_prices 정상 적재 검증 완료[cite: 1]

## 아키텍처 리팩토링 및 배포 자동화

- [x] `core/calculator.py` 분산 리팩토링: `core/valuator/{deposit, fund, stock, crypto}.py` 분리[cite: 1]
- [x] 운영 배포 인프라 구축 (멀티스테이지 `Dockerfile`, 운영용 `docker-compose.yml`, GitHub Actions CI/CD)[cite: 1, 2]
- [x] APScheduler 도입 (`AsyncIOScheduler`, 평일 오전 10:30, 장 마감 16:00, 주간 결산 토 10:00)[cite: 1]
- [x] Makefile 및 Watchtower 서비스 설정 추가 (Git log 반영)
