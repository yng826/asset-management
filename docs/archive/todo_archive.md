# 작업 완료 아카이브 (Historical Archive)

> 카테고리별로 정리하고, 각 항목 앞에 완료일(커밋 기준)을 적는다. 섹션 안에서는 최신 항목이 위로 온다.
> TODO.md 의 "완료된 직전 작업"이 다음 작업으로 밀려나면 해당 카테고리 맨 위로 옮긴다.

## 거래 입력 · 원장

- [x] 2026-10-09 계좌 예수금 잔액 대사 `/recon` (전체 메뉴 '🧮 잔액 대사', `bot/handlers/recon_handler.py`)
  - 계좌(원장 예수금 표시) → 통화(USD 거래 이력 계좌) → 실제 예수금 입력 → 차액 확인 → 기록 방식 선택 → 오늘 일자 저장 (메모 '잔액 대사 보정')
  - 차액 +: 이자·기타 수익(DIVIDEND, `CASH_*`, 수익률·배당 포함) / 입금 보정(DEPOSIT), 차액 −: 출금 보정(WITHDRAW), 일치하면 기록 없음
- [x] 2026-10-09 텔레그램 버튼 단계 거래 입력 `/trade` 및 종목 마스터 (개발·운영 DB 적용 완료)
  - 계좌 → 유형 → 종목(보유 종목 버튼 / 이름 검색) → 수량 → 단가(배당·입출금은 금액) → 일자 → 확인 카드 → 저장 (`[버튼입력]` 메모), 최근 종가 대비 단가 ±20% 경고
  - 종목 마스터 `ticker_master`(국내 주식·ETF·미국·업비트 약 1.1만 건): 일요일 07:00 동기화, 기동 시 비어 있으면 적재. 검색은 과거 거래 종목 우선·별칭(`TICKER_ALIASES`)·미국 티커 시세 확인
  - 미래에셋 일반 AAPL 초기잔고 통화 KRW → USD 보정 (`scripts/migrate_aapl_currency.py`)
- [x] 2026-10-09 거래 원장 정합성 정리 및 현금 계산 정식 장부 방식 전환 (개발·운영 DB 적용 완료)
  - 재발 방지: `ASSET_MAP` 정식 계좌명 통일 + `ACCOUNT_ALIASES`/`DEFAULT_ACCOUNT`("토스 일반"), 파서 출력 보정(`_normalize_items`: 계좌명 정식화, `TICKER_MAP` 코드 강제, 단가 재계산)
  - 데이터 정리(`scripts/migrate_ledger_cleanup.py`, 백업 `transactions_backup_20261009`): 계좌명 통합, 토스 거래 오배정(id 80·82·83·84), 종목코드(id 89~92), USD 통화 5건, 단가 4건, 초기 현금 행 BUY→DEPOSIT
  - 현금 계산 임시 규칙 제거 + "초기 현금 보정" DEPOSIT 11건, 실제 잔액 대비 "잔액 대사 보정" 10건(2026-10-09), 카카오 일반 관리 대상 제외
  - 2026-05-01 ~ 10-09 스냅샷 재계산 (04월 이전 스냅샷은 부정확함을 감안하고 보존, 오수집된 371450 시세 1행 잔존)
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
- [x] 2026-10-09 성과 분석용 시세 공백 보정 (개발·운영 DB 적용 완료)
  - 국내 05-01~05-03 원가 평가(국내 시세 05-04부터) → 04-24~04-30 국내 종가 백필
  - `0181L0` 영숫자 단축코드가 `backfill_daily_prices.py` 정규식에서 빠짐 → `[0-9][0-9A-Z]{5}`로 보완, 04-24~09-03 백필
  - 펀드 4종 NAV 공백(초기잔고가 과거 매수원가 → 09-04 평가이익 +605만 일시 반영) → 금융투자협회 공시 기준가 추이 API 백필 스크립트 `scripts/backfill_fund_nav.py` (펀드닥터 수집값과 일치)
  - 05-01~09-03 스냅샷 재계산: 대상 5종목 평가액만 변경 확인, TWR 누적 +2.01% → +0.88%
  - 그 외 점검: 미국주식 시차(08:55 최근 4일 재계산으로 정상), 주말 변동(코인·환율), 큰 변동일(07-28·07-31 등 실제 시장 변동)
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

## 성과 분석

- [x] 2026-10-09 5~8월 추정 구간 표시: 원장 복원 대신 유지 결정 (5~6월 손절 후 같은 금액 재매수 → 총액은 비슷하고 손실만 희석된 수치로 해석)
  - `core/performance.py` `LEDGER_TRACKING_START = 2026-09-01`(실제 거래 기록 시작일), 수익률 비교·낙폭·환율 차트에 09-01 이전 회색 음영 + "estimated", 월별 손익 5~8월 막대 옅게·`(est)`, 캡션에 추정 안내
  - 배당 "최근 12개월" → 기록 기간(09-01 이후) 합계 + 연환산 평가액 대비 % (0.07% → 0.63%)
- [x] 2026-10-09 성과 분석·차트 고도화 (`core/performance.py`, `/chart` 종류 버튼 확장)
  - TWR 일간 수익률 `get_twr_series()`: 입출금 제외(USD는 거래일 환율), 배당은 수익, 성과 측정 시작일 `PERFORMANCE_INCEPTION_DATE = 2026-05-01`. `/chart` 수익률 비교를 TWR로 교체(시작일 이전은 벤치마크도 함께 잘라 기준 일치)
  - 낙폭·리스크 `/chart dd`: MDD·연환산 변동성·샤프(무위험 2.5%)·고점/저점/회복일, KOSPI·S&P500 동일 지표 비교
  - 월별 손익 `/chart month`: 월 손익(입출금 제외)·월간 TWR, 누적선, 진행 중 월 표시
  - 종목 기여도 `/chart contrib`: 종료 평가 − 시작 평가 − 매수 + 매도 + 배당, 현금은 잔차 → 합계가 월별 손익과 일치
  - 환율 효과 `/chart fx`: 미국 직접투자·달러 예수금의 주가 효과 / 환율 효과 일별 분해
  - 배당 현황 `/chart div [연도]`: 월별 종목 누적 막대·연간 누적선, 종목·계좌별·최근 12개월
  - 차트 한글 라벨용 `fonts-nanum` (Dockerfile·Dockerfile.dev), 폰트 없으면 종목코드로 대체
- [x] 2026-10-09 주간 성과 리포트 (`bot/weekly_report.py`, 일요일 08:00 자동 발송, `/weekly`·메뉴 '🗓 주간 리포트')
  - 총자산·주간 손익·TWR, KOSPI·S&P500·BTC, 이번 달·누적, 현재 낙폭·MDD, 기여 상위·하위, 환율 효과, 배당 입금, 목표 비중 이탈 + 차트 2장(주간 기여도, 3개월 수익률 비교)

## 리포트 · 차트 · 텔레그램 명령어

- [x] 2026-10-09 `/chart alloc [기간]` 직접 입력을 버튼 경로와 통일 (기본 1개월·어제까지, 캡션·빈 데이터 안내·오류 처리). 금액 스택으로 대체 가능해 차트 메뉴에서 '🥧 자산 배분' 버튼을 맨 뒤로
- [x] 2026-10-09 텔레그램 첫 화면 정리
  - 하단 고정 키보드(자산 요약 / 실시간 / 손익 / 거래 입력 / 메뉴), 조회 버튼은 group -1에서 처리해 거래 입력 진행 단계 유지
  - `BOT_COMMANDS`: start·status·live·pnl·trade·weekly (나머지는 전체 메뉴 버튼·직접 입력), 기동 시 `post_init` 직접 호출로 명령어 동기화
- [x] 2026-10-09 조회 버튼화: `/start` 메인 메뉴, `/pnl` 기간 버튼, `/live` 자산군·새로고침, `/chart` 종류 → 기간 버튼
- [x] 2026-10-09 리포트 손익 기준 통일 및 시세 확정값 기록 체계 정비 (개발·운영 DB 적용 완료)
  - `/status`: 계좌별 예수금·총자산 표시, 계좌 유형(일반/연금·절세/가상자산)별 묶음, 기준 시세 일자 범위·시세 지연 경고, 환율(적용일) 표시
  - "오늘 손익" → "최근 결산 손익"으로 통일: 16:00 결산(`closing_1600`) 확정 스냅샷 기준, `/status`·`/breakdown`·`/pnl` 수치 일치 (장중 변동은 `/live`)
  - `/breakdown`: `v_latest_asset_breakdown` 뷰(해외주식 USD 미환산·매도 미차감·예수금 누락) 대신 `/status` 평가 결과로 자산군 집계, 결산일 자산군별 손익 분해(합계 = `/pnl`)
  - `/pnl`: 16:00 결산 전 당일 행 "(잠정)" 표기, `get_daily_pnl_history` `self` 누락 버그 수정
  - `/live` 코인: 등락 기준을 업비트 전일 종가(09:00 KST)로 변경
  - 가상자산 시세: 장중 현재가 대신 확정 일봉 종가만 기록(매일 09:01 수집), 결산 스냅샷 D는 D-1 종가 사용 → 09-10~10-08 코인 시세 재적재 및 04-01~10-08 스냅샷 재계산 (`/pnl` 과거값 변경)
  - 국내 시세: 최근 5영업일 종가 덮어쓰기(16:00 미확정 종가 보정) + 평일 08:55 최근 4일 결산 스냅샷 재계산(`refresh_recent_snapshots`)
  - 지수: FDR KRX 소스 장애(09-18~)로 KOSPI·KOSDAQ Yahoo 소스 전환, 09-18~10-08 백필
  - `daily_holding_snapshots` 일자 단위 교체 저장(계좌명 변경 시 옛 행 잔존 해소), `repair_history` 종료일 기본값 어제·미확정 일봉/정기예금 제외
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

- [x] 2026-10-09 `/alert` 감시 기준값 조회·변경 (`bot/handlers/alert_handler.py`, 메뉴 '🚨 알림 기준')
  - 기준값 9종 목록 + 항목별 ➖/➕ 버튼, `/alert set 국내급락 4`(별칭·범위 검증), `/alert reset 항목|all`, `/alert log`(최근 7일 발송 기록)
- [x] 2026-10-09 목표 비중 리밸런싱 알림 (`core/rebalance.py`, `/target`, 메뉴 '⚖️ 목표 비중')
  - 목표 `rebalance_targets`(첫 사용 시 자동 생성), 허용폭 `detector_settings.rebalance_band_pct`(기본 ±5%p), 현황에 목표까지 매수·매도 금액
  - 매일 16:10 이탈 알림(같은 자산군·방향 7일간 재알림 안 함, `anomaly_alert_logs` `REBALANCE_DRIFT`)
- [x] 2026-10-09 이상징후 감시 기준값 DB화(`detector_settings`), 중복 알림 방지(`anomaly_alert_logs`), 미국주식·코인 24시간 감시
- [x] 2026-09-09 이상징후 감시 로직 `core/detector/` 패키지로 분리 및 `core/scheduler.py` 다이어트
- [x] 2026-09-09 프리마켓 및 장중 이상징후 감시

## 스케줄러 · 배치

- [x] 2026-10-09 배치 감사 로그 상태 판정 정비: `core.health.judge_batch`로 배치가 책임지는 자산군 시세 최신성(지수 최신 거래일 기준) 판정 → 휴장일 결산 WARNING 오탐 제거, 오전 브리핑도 SUCCESS/WARNING 판정, 수집 예외는 FAILED(이전엔 삼켜짐), 지연 항목은 message에 기록. `/health` 배치 항목이 마지막 실행의 FAILED/WARNING도 표시 (과거 기록은 그대로)
- [x] 2026-10-09 봇 상태 점검 `/health` + 매일 16:30 자동 점검 (Prometheus/Grafana 대신 경량 방식, `core/health.py`, 메뉴 '🩺 상태 점검')
  - 배치(16:00 결산·평일 08:55 브리핑) 실행 여부, 결산 스냅샷, 보유 종목 자산군별 시세 최신성(KOSPI·S&P500 최신 거래일 기준이라 휴장일 오탐 없음), 환율, 지수 수집 중단, 최근 24시간 에러 로그(재시작 시 CancelledError 제외)
  - 16:30 점검은 문제 항목만 알림, 같은 항목은 하루 1회 (`anomaly_alert_logs` event_type `HEALTH`)
- [x] 2026-09-10 모닝 브리핑 수집 시간대 최적화 및 08:30 조기 수집 검증용 사전 프로브(Probe) 스케줄
- [x] 2026-09-08 배치 실행 로그(`batch_execution_logs`) 및 정기 리포트 발송 감사 로그 기록
- [x] 2026-09-05 APScheduler 도입 (`AsyncIOScheduler`, 평일 10:30 / 장 마감 16:00 / 주간 결산 토 10:00)

## 코드 구조 · 리팩토링

- [x] 2026-10-09 DB 접근·중복 코드 공통화 리팩토링 (커밋 7개, 동작 변경 없음)
  - 검증: 개발 DB 고정 일자(10-09 17:00) 출력·차트 PNG 해시·`/chart` 핸들러 응답·trade/recon 대화 흐름·가짜 커넥션 SQL 기록 스냅샷을 전후 비교 (의도한 차이는 커밋 메시지에 기록)
  - `database/connection.py`: `fetch_all`(strict)·`execute`(실행 여부 bool)·`execute_many`·`read_df`(strict), 연결 실패 구분용 `DBConnectionError` → `core`·`bot`에서 `get_connection()` 직접 사용 제거
  - `database/repository.py`: `@_on_error(기본값, 메시지)` + 공통 헬퍼로 보일러플레이트 정리(955 → 754줄), 종목명 매핑 `get_ticker_name_map`·알림 중복 방지 `has_recent_anomaly_alert`/`record_anomaly_alert` 공통화
  - `core/performance.py`: USD→KRW 환산을 `_usd_krw_rates`/`_to_krw` 하나로, `core/calculator.py`·scheduler·fetcher도 공통 헬퍼로
  - 차트: `bot/chart_renderer.py` 공통 헬퍼(`_to_png`·`_format_date_axis`·`_use_korean_font`), `report_handler` 기간 파싱 `_period_start_date`·응답 흐름 `_send_chart`
  - 대화: `bot/handlers/conversation.py` (trade/recon 렌더링·계좌 버튼·취소·지난 버튼·저장 응답·ConversationHandler 조립)
  - scripts: 조회 `fetch_all`, 일괄 쓰기 `execute_many`. 원자성이 필요한 트랜잭션(마이그레이션 2개, `replace_ticker_master`·`save_holding_snapshots`)은 직접 커넥션 유지
  - 발견: `get_connection()`이 `autocommit=True`라 스크립트의 `commit()`/`rollback()`은 효과가 없었음 (정리), 쿼리 예외 시 커넥션 미반환 해소

## 인프라 · 배포 · 개발 환경

- [x] 2026-10-09 로그 파일 회전: `main.py` `RotatingFileHandler`(5MB × 5개 보관, 최대 약 30MB), `/health` 에러 로그 점검은 `app.log.1`도 함께 읽음, `prod.sh app-logs`는 `tail -F`로 회전 후에도 계속 추적
- [x] 2026-10-09 개발 컨테이너의 운영 `.env` 읽기 차단
  - `config/settings.py` `ENV_FILE`(기본 `.env`)로 읽을 파일 선택, `.dev`인데 `DB_NAME`이 `*_dev`가 아니면 기동 중단
  - `docker-compose.dev.yml`: `ENV_FILE=.env.dev`, 운영 `.env`를 `/dev/null`로 가림. 호스트 스크립트는 `ENV_FILE=.env.dev python -m scripts.<name>`
- [x] 2026-10-09 운영 DB 일일 백업 스크립트 (`scripts/backup_db.sh`: gzip 덤프·무결성 검사·90일 보관·실패 시 텔레그램 알림, 운영 서버 crontab 매일 04:00 등록)
- [x] 2026-10-09 개발/운영 DB 분리 (`asset_management_dev` + `asset_dev` 계정, 운영 조회 전용 `asset_ro` 계정, 운영 데이터 덤프로 개발 DB 구성)
- [x] 2026-10-09 `schema.sql` 실제 DB와 동기화 (`v_daily_asset_class_summary`, `daily_holding_snapshots`), `daily_prices.close_price` DECIMAL(15,4) 마이그레이션, `init_tables` 주석 뒤 쿼리 스킵 버그 수정
- [x] 2026-09-10 `scripts/dev_lint.sh` fix 옵션 보강 (`ruff check --fix --unsafe-fixes` 및 format 통합)
- [x] 2026-09-06 Makefile 추가 (코드 검사 및 자동 수정 명령어)
- [x] 2026-09-05 운영 배포 인프라 및 CI/CD 구축 (멀티스테이지 `Dockerfile`, 운영용 `docker-compose.yml`, GitHub Actions, Watchtower)
- [x] 2026-09-05 코드 품질 자동 검사 프로세스 수립 (`ruff`, `pyproject.toml`, `scripts/dev_lint.sh`)
- [x] 2026-09-05 핫 리로드 지원 개발용 Docker 환경 (`scripts/dev.sh`, watchdog)
