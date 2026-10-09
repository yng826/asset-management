# TODO.md

## 현재 진행할 작업
* [ ] 실제 목표 비중 입력 (`/target set …`, 사용자 결정)
* [ ] 배당·이자: 건별 입력 대신 월 1회 `/recon`(💰 이자·기타 수익)으로 반영
* [ ] 리팩토링 (동작 변경 없음, 개발 DB 고정 일자 출력 스냅샷 전후 완전 일치로 검증)
  - [x] 1단계 데이터 접근 공통화 (2026-10-09)
  - [x] 2단계 `bot/chart_renderer.py` 공통 헬퍼(`_to_png`·`_format_date_axis`·`_use_korean_font`·`BENCHMARK_NAMES`), `report_handler` 기간 파싱(`_period_start_date`)·`_send_chart` 통합 (2026-10-09)
  - [ ] `/chart alloc` 명령 인자 경로는 버튼 경로(`_handle_allocation_chart`)와 달리 전체 기간·캡션 없음·예외 처리 없음 → 통일 여부 결정 (동작 변경이라 리팩토링에서 제외)
  - [x] 3단계 trade/recon 대화 공통 함수 → `bot/handlers/conversation.py` (렌더링·계좌 버튼·취소·지난 버튼·저장 응답·숫자 파싱·ConversationHandler 조립) (2026-10-09)
  - [x] `core/calculator.py` DB 접근 공통화 (`pd.read_sql_query` 3곳·직접 커넥션 5곳 → `read_df`/`fetch_all`, `read_df(strict=)` 추가) (2026-10-09)
  - [x] `core/scheduler.py`·`core/price_fetcher.py`·`core/fetcher/{fx,kr_stock}.py` DB 접근 공통화 (`execute`가 실행 여부 bool 반환) → `core`·`bot`에 `get_connection()` 직접 사용 없음 (2026-10-09)
  - [ ] (별도) `database/repository.py` 메서드별 커넥션·try/except 보일러플레이트 정리

## 완료된 직전 작업
- [x] 2026-10-09 리팩토링 1단계 데이터 접근 공통화: `database/connection.py` `fetch_all`(strict 옵션)·`execute`·`read_df`, `AssetRepository.get_ticker_name_map`(최신 거래 종목명, `/alert log`도 통일)·`has_recent_anomaly_alert`·`record_anomaly_alert`, 환율 조회는 `performance._usd_krw_rates`/`_to_krw` 하나로
- [x] 2026-10-09 5~8월 추정 구간 표시: 원장 복원 대신 유지 결정 (5~6월 손절 후 같은 금액 재매수 → 총액은 비슷하고 손실만 희석된 수치로 해석)
  - `core/performance.py` `LEDGER_TRACKING_START = 2026-09-01`(실제 거래 기록 시작일), 수익률 비교·낙폭·환율 차트에 09-01 이전 회색 음영 + "estimated", 월별 손익 5~8월 막대 옅게·`(est)`, 캡션에 추정 안내
  - 배당 "최근 12개월" → 기록 기간(09-01 이후) 합계 + 연환산 평가액 대비 % (0.07% → 0.63%)
- [x] 2026-10-09 배치 감사 로그 상태 판정 정비: `core.health.judge_batch`로 배치가 책임지는 자산군 시세 최신성(지수 최신 거래일 기준) 판정 → 휴장일 결산 WARNING 오탐 제거, 오전 브리핑도 SUCCESS/WARNING 판정, 수집 예외는 FAILED(이전엔 삼켜짐), 지연 항목은 message에 기록. `/health` 배치 항목이 마지막 실행의 FAILED/WARNING도 표시 (과거 기록은 그대로)
- [x] 2026-10-09 로그 파일 회전: `main.py` `RotatingFileHandler`(5MB × 5개 보관, 최대 약 30MB), `/health` 에러 로그 점검은 `app.log.1`도 함께 읽음, `prod.sh app-logs`는 `tail -F`로 회전 후에도 계속 추적
- [x] 2026-10-09 봇 상태 점검 `/health` + 매일 16:30 자동 점검 (Prometheus/Grafana 대신 경량 방식, `core/health.py`, 메뉴 '🩺 상태 점검')
  - 배치(16:00 결산·평일 08:55 브리핑) 실행 여부, 결산 스냅샷, 보유 종목 자산군별 시세 최신성(KOSPI·S&P500 최신 거래일 기준이라 휴장일 오탐 없음), 환율, 지수 수집 중단, 최근 24시간 에러 로그(재시작 시 CancelledError 제외)
  - 16:30 점검은 문제 항목만 알림, 같은 항목은 하루 1회 (`anomaly_alert_logs` event_type `HEALTH`)
- [x] 2026-10-09 계좌 예수금 잔액 대사 `/recon`, 개발 컨테이너 운영 `.env` 읽기 차단
- [x] 2026-10-09 성과 분석·차트 고도화(TWR·낙폭·월별·기여도·환율·배당), 주간 리포트, `/alert`, 목표 비중 리밸런싱 알림 (봇 실기동 확인, 운영 배포·한글 폰트·시세 공백 보정 반영 완료)
  - 상세 내역은 `docs/archive/todo_archive.md` (성과 분석 / 이상징후 감시 / 거래 입력 · 원장 / 인프라)

## 근미래 작업 (Next Steps)
* [ ] 장중 FDR 당일 행 제공 여부 검증 (국내 정규장 / 미국 정규장 중 실제 알림 발생 확인)
* [ ] 미사용 `v_latest_asset_breakdown` 뷰 정리 여부 결정 (`/breakdown`은 Python 집계로 전환됨, `get_latest_asset_class_summary`만 참조)
* [ ] 운영 서버 `.venv`를 `requirements.txt`와 동기화 (스크립트 실행 시 `pyupbit` 누락 발생), 스크립트는 `python -m scripts.<name>`으로 실행
* [ ] 일일 펀드 수집 `collect_fund_prices`에 KOFIA 기준가 API를 보조 소스로 추가 검토 (펀드닥터 장애 대비)
* [ ] `/pnl` 하단 리스크 요약 한 줄(현재 낙폭·MDD) 추가 여부 결정

## 보류
* [ ] (보류 2026-10-09) 증권사 캡처 이미지 → Gemini Vision 추출 → 확인 카드 저장
  - 버튼 단계 입력(종목 검색·단가 경고·확인 카드)으로 입력 부담이 해소되어 우선순위 하락. 다건 체결 일괄 입력 수요가 생기면 재검토
* [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합 (시장 전체 하락인지 개별 종목 이슈인지 한 줄)