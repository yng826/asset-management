# TODO.md

## 현재 진행할 작업
- [ ] 운영 반영 (텔레그램 버튼 UI·종목 마스터·AAPL 통화 보정)
  - [ ] 배포 후 `ticker_master` 자동 생성·적재 확인 (수동: `python -m scripts.sync_ticker_master`), `/start` → '거래 입력' 동작 확인
  - [ ] 운영 DB `python -m scripts.migrate_aapl_currency` (dry-run 확인 후 `--apply`) → `python -m scripts.backfill_snapshots 2026-05-01` (개발 DB 적용 완료)
- [ ] 텔레그램 첫 화면 정리
  - [ ] 하단 고정 키보드(ReplyKeyboard)로 자주 쓰는 버튼 상시 노출 (예: 자산 요약 / 실시간 / 손익 / 거래 입력 / 메뉴), 버튼 텍스트는 Gemini 자유 입력·거래 입력 대화보다 먼저 가로채기
  - [ ] BotFather 명령어 목록 축소 (`BOT_COMMANDS`: start·status·live·pnl·trade 위주, 나머지는 메뉴 버튼으로)
  - [ ] BotFather 목록의 `/cancel`(마지막 거래 취소), `/dividend` 핸들러 미구현 상태 정리 (구현 또는 목록 제거)
- [ ] (검토) 증권사 캡처 이미지 → Gemini Vision 추출 → 종목 마스터 검증 → 버튼 입력과 동일한 확인 카드로 저장

## 완료된 직전 작업
- [x] 2026-10-09 텔레그램 버튼 UI 및 버튼 단계 거래 입력 (개발 DB `ticker_master` 적재 완료, 운영 미배포)
  - 조회 버튼화: `/start` 메인 메뉴, `/pnl` 기간 버튼(1주·2주·1개월·3개월, 같은 메시지 갱신, `/pnl 14`·`2w`·`1m` 직접 입력 유지), `/live` 자산군·새로고침 버튼, `/chart` 종류→기간 버튼 (인자 없는 `/chart`는 선택 화면)
  - `/trade`·메뉴 '거래 입력': 계좌 → 유형 → 종목(계좌 보유 종목 버튼 / 이름 검색) → 수량 → 단가(배당·입출금은 금액) → 일자 → 확인 카드 → 저장 (`[버튼입력]` 메모)
  - 종목 마스터 `ticker_master`(국내 주식·ETF·미국·업비트 약 1.1만 건): 일요일 07:00 주간 동기화, 기동 시 비어 있으면 적재, 시장별 최소 건수 미달 시 기존 유지
  - 검색: 단어 단위 포함 검색, 과거 거래 종목·이름 우선, 별칭(`TICKER_ALIASES`: 삼전·엔비디아 등), 목록에 없는 미국 티커는 시세 조회로 확인 후 직접 지정
  - 확인 단계에서 최근 종가 대비 단가 ±20% 이상이면 경고
  - 미래에셋 일반 AAPL 초기잔고 통화 KRW → USD 보정 스크립트(`scripts/migrate_aapl_currency.py`, 초기 현금 보정 USD 분리) — 개발 DB 적용·스냅샷 재계산 완료

## 근미래 작업 (Next Steps)
* [ ] 시차 자산(미국주식 T+1, 펀드 NAV T+1/T+2) 시세 수집 시점 및 일일 스냅샷 정합성 검증
  - 미국 주식/펀드의 직전 유효 시세(Latest Available Price) Fallback 매커니즘 점검
  - 휴장일(공휴일/주말) 및 시차 반영 시 daily_snapshots 평가액 왜곡 방지 로직 확인
* [ ] 텔레그램 커맨드로 이상징후 감시 기준값(detector_settings) 조회/변경 기능 추가
  - 예: `/alert` 현재 기준값 목록, `/alert set kr_prev_close_drop_pct 4.0` 변경 (허용 키·값 범위 검증)
* [ ] 장중 FDR 당일 행 제공 여부 검증 (국내 정규장 / 미국 정규장 중 실제 알림 발생 확인)
* [ ] 미사용 `v_latest_asset_breakdown` 뷰 정리 여부 결정 (`/breakdown`은 Python 집계로 전환됨, `get_latest_asset_class_summary`만 참조)
* [ ] 개발 컨테이너가 바인드 마운트된 운영 `.env`를 `load_dotenv()`로 함께 읽는 문제 차단 (`.env.dev`에 없는 키는 운영 값으로 대체됨)
* [ ] 운영 서버 `.venv`를 `requirements.txt`와 동기화 (스크립트 실행 시 `pyupbit` 누락 발생), 스크립트는 `python -m scripts.<name>`으로 실행
* [ ] 계좌 잔액 대사(Reconciliation) 기능: 실제 현금잔액 입력 시 원장과의 차액을 해당 일자 보정 거래(DEPOSIT/WITHDRAW)로 기록
  - 누락된 이자·소액 배당 등을 주기적으로 흡수 (텔레그램 커맨드 또는 CSV 입력)
* [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
* [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
* [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동