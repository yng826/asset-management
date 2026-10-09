# 작업 완료 아카이브 (Historical Archive)

> 카테고리별로 정리하고, 각 항목 앞에 완료일(커밋 기준)을 적는다. 섹션 안에서는 최신 항목이 위로 온다.
> TODO.md 의 "완료된 직전 작업"이 다음 작업으로 밀려나면 해당 카테고리 맨 위로 옮긴다.

## 거래 입력 · 원장

- [x] 2026-10-08 입출금 거래 시 통화에 따라 종목명·코드 자동 설정 (KRW_CASH 등)
- [x] 2026-10-07 자연어 거래 파서(TransactionParser) 개선
  - 다건 거래의 상대일자 상속, 기본 계좌 자동 매핑, 통화(`currency`) 컬럼 지원
  - 다건 거래(텍스트/음성) 입력 연동, 거래 입력 변환 가이드 문서(`trade_guide.md`) 추가
- [x] 2026-09-05 초기 잔고 CSV 임포트 스크립트 (`scripts/import_initial_csv.py`)
- [x] 2026-09-05 `/history` 최근 거래 내역 조회
- [x] 2026-09-05 텔레그램 텍스트·음성 거래 기록 처리 (Gemini 기반 TransactionParser)

## 시세 수집

### 국내 · 해외 주식 / 지수 · 환율 / 가상자산
- [x] 2026-10-07 시세 반영 지연 검증 스크립트 (`scripts/verify_price_lag.py`)
- [x] 2026-09-07 시세 수집기 `core/fetcher` 모듈화 및 자산군별 수집 스크립트(`scripts/fetch_price.py`) 추가
- [x] 2026-09-06 가상자산(Upbit API) 시세 수집기 및 평가 연동
  - `core/valuator/crypto.py`: Upbit 보유 코인 평가 로직 실구현
  - Upbit Public Ticker API 연동 (KRW 마켓 현재가 수집)
  - `/status`, `/details` 출력 시 가상자산 정상 평가액 반영 확인
- [x] 2026-09-06 주요 벤치마크 지수 및 환율 수집 (`daily_prices` 연동)
  - 국내: 코스피(`KS11`), 코스닥(`KQ11`) / 해외: S&P500(`US500`), 나스닥(`IXIC`), 다우존스(`DJI`) / 환율: 원/달러(`USD/KRW`)
  - FDR 기반 지수/환율 수집 함수 구현 및 일일 스케줄러 연동
  - `scripts/backfill_indices.py`: 최근 1년치 지수/환율 일봉 일괄 적재
  - 기존 holdings 기반 평가 로직(`core/valuator/*`, `core/calculator.py`)과의 격리 검증
- [x] 2026-09-05 해외주식(직투) 시세 및 USD/KRW 환율 연동
- [x] 2026-09-05 FDR 기반 국내 주식/ETF 당일 종가 수집 및 `daily_prices` UPSERT
  - 영숫자 혼용 6자리 ETF 단축코드(0181L0 등) 수집 처리 및 16개 종목 수집 검증

### 펀드 NAV 수집 변천사
- [x] 2026-09-10 펀드닥터 스크래퍼 응답 인코딩 UTF-8 명시 및 수익률/기준가 계산식 보정
- [x] 2026-09-07 펀드닥터 수집 URL 교정
- [x] 2026-09-05 펀드닥터(funddoctor.co.kr) HTML 크롤러로 최종 안착 — 4종 펀드 자동 수집 및 `daily_prices` 적재 검증
- [x] 2026-09-05 FunETF 내부 세션 기반 API 연동 시도 (환경별 차단 이슈로 전환)
- [x] 2026-09-05 공공데이터포털(금융위원회 증권정보 Open API) 연동 시도 (표준코드 매핑 확인, NAV 미제공 한계로 전환)
- [x] 2026-09-05 IRP 퇴직연금 펀드 기준가(NAV) 수집 및 평가 연동 (1차: 네이버 금융/오버라이드 방식)

### 과거 데이터 백필
- [x] 2026-09-06 `daily_prices` 과거 시세 백필 (`scripts/backfill_daily_prices.py`)
  - 주식/ETF: 2026-05-01 ~ 현재 (FDR)
  - 가상자산: 2025-01-01 ~ 현재 (Upbit API 페이징, 약 600일치)
  - 지수/환율: 1년치 (`scripts/backfill_indices.py`)

## 평가 · 스냅샷

- [x] 2026-10-08 특정 날짜 기준 총자산 및 종목별 세부 스냅샷 저장
- [x] 2026-10-08 일별 스냅샷 순현금 집계 로직 정상화 (가상 CASH 티커 필터링 제거, 거래 원장 기반 `get_cash_balances_by_currency` 연동)
- [x] 2026-09-10 정규 배치 시작 시 과거 누락 스냅샷 자동 감지 및 자가 치유(Auto-healing) 루틴
- [x] 2026-09-09 세부 스냅샷 정합성 보정 및 포트폴리오 다차원 분석 VIEW 추가 (`v_daily_account_summary`, `v_daily_asset_class_summary`, `v_latest_holding_ranking`)
- [x] 2026-09-09 단가 기준 등락률 계산 로직 추가
- [x] 2026-09-08 `daily_holding_snapshots` 계좌·종목 단위 세부 스냅샷 테이블 및 벌크 UPSERT 파이프라인
- [x] 2026-09-06 일별 총자산 스냅샷 집계 배치 (`daily_snapshots`) 및 백필 (`scripts/backfill_snapshots.py`, `transactions` × `daily_prices` 조인, 2026-05-01~)
- [x] 2026-09-06 개별 종목 수익률 계산 함수 `get_single_asset_performance`
- [x] 2026-09-05 평가 로직 분산 리팩토링: `core/valuator/{deposit, fund, stock, crypto}.py`
- [x] 2026-09-05 정기예금 평가액 계산 (`is_deposit`, `parse_deposit_metadata`, `calculate_deposit_valuation`)
  - 시세 수집 없이 원금 × 이율 × 경과일수 / 365 일할 계산으로 자동 반영
- [x] 2026-09-05 `core/calculator.py`: holdings ↔ `daily_prices` 가격 매핑 / 계좌·전체 집계

## 리포트 · 차트 · 텔레그램 명령어

- [x] 2026-10-08 차트 스타일링 개선 및 날짜 포맷 설정
- [x] 2026-10-07 실시간 자산 상태 메시지 개선 및 손익 텍스트 포맷 수정
- [x] 2026-09-18 비교 차트 기본 날짜 계산 수정 및 차트 데이터 유효성 검사
- [x] 2026-09-11 자산군별 절대금액 스택 바 차트
- [x] 2026-09-10 텔레그램 `/breakdown` 자산군별 비중 리포트
  - 신규 DB 뷰 `v_latest_asset_breakdown` 기반 조회 최적화
  - 어제 대비 자산군별 변동 내역(금액/등락률) 추가
- [x] 2026-09-10 자산 배분 누적 면적 차트 (`/chart alloc`)
- [x] 2026-09-10 세부 스냅샷 기반 계좌별 비중 텍스트 요약 커맨드
- [x] 2026-09-10 텔레그램 명령어 목록 동기화 스크립트 (`scripts/sync_commands.py`)
- [x] 2026-09-08 `/live` 가상자산·멀티 자산 실시간 현황 조회
- [x] 2026-09-07 `/pnl` 일별 손익 요약 (MariaDB `LAG()` 기반)
- [x] 2026-09-07 텔레그램 메시지 HTML 모드 전환 및 안전한 문자열 처리
- [x] 2026-09-06 `/log` 명령어 및 관리자 확인 함수
- [x] 2026-09-06 `/chart` 자산 수익률 vs 지수 비교 차트 (Matplotlib 정규화 렌더러, 기간별 파싱)
- [x] 2026-09-05 `/status` UX 개편 — 한 페이지 요약 + `/details` 신설
  - `core/calculator.py`: `build_status_summary()` 신규 (모바일 1-페이지 압축, 단일 메시지)
  - `bot/handlers/report_handler.py`: `status_command` 단일 메시지 / `details_command` 상세 다중 청크
  - `bot/bot.py`: `/details` 핸들러 등록 및 안내 갱신
- [x] 2026-09-05 `/status` 평가액·수익률 출력 및 `build_status_chunks()` 기반 자동 분할 전송

## 이상징후 감시

- [x] 2026-10-09 이상징후 감시 기준값 DB화(`detector_settings`), 중복 알림 방지(`anomaly_alert_logs`), 미국주식·코인 24시간 감시
- [x] 2026-09-09 이상징후 감시 로직 `core/detector/` 패키지로 분리 및 `core/scheduler.py` 다이어트
- [x] 2026-09-09 프리마켓 및 장중 이상징후 감시

## 스케줄러 · 배치

- [x] 2026-09-10 모닝 브리핑 수집 시간대 최적화 및 08:30 조기 수집 검증용 사전 프로브(Probe) 스케줄
- [x] 2026-09-08 배치 실행 로그(`batch_execution_logs`) 및 정기 리포트 발송 감사 로그 기록
- [x] 2026-09-05 APScheduler 도입 (`AsyncIOScheduler`, 평일 10:30 / 장 마감 16:00 / 주간 결산 토 10:00)

## 인프라 · 배포 · 개발 환경

- [x] 2026-10-09 운영 DB 일일 백업 스크립트 (`scripts/backup_db.sh`: gzip 덤프·무결성 검사·90일 보관·실패 시 텔레그램 알림, 운영 서버 crontab 매일 04:00 등록)
- [x] 2026-10-09 개발/운영 DB 분리 (`asset_management_dev` + `asset_dev` 계정, 운영 조회 전용 `asset_ro` 계정, 운영 데이터 덤프로 개발 DB 구성)
- [x] 2026-10-09 `schema.sql` 실제 DB와 동기화 (`v_daily_asset_class_summary`, `daily_holding_snapshots`), `daily_prices.close_price` DECIMAL(15,4) 마이그레이션, `init_tables` 주석 뒤 쿼리 스킵 버그 수정
- [x] 2026-09-10 `scripts/dev_lint.sh` fix 옵션 보강 (`ruff check --fix --unsafe-fixes` 및 format 통합)
- [x] 2026-09-06 Makefile 추가 (코드 검사 및 자동 수정 명령어)
- [x] 2026-09-05 운영 배포 인프라 및 CI/CD 구축 (멀티스테이지 `Dockerfile`, 운영용 `docker-compose.yml`, GitHub Actions, Watchtower)
- [x] 2026-09-05 코드 품질 자동 검사 프로세스 수립 (`ruff`, `pyproject.toml`, `scripts/dev_lint.sh`)
- [x] 2026-09-05 핫 리로드 지원 개발용 Docker 환경 (`scripts/dev.sh`, watchdog)
