# TODO.md

## 현재 진행할 작업
- [ ] 거래 원장 정합성 정리 및 현금 계산 정식 장부 방식(B안) 전환 — 개발 DB에서 먼저 적용·검증 후 운영 반영
  - 재발 방지 (코드)
    - [ ] `config/constants.py` `ASSET_MAP` 계좌명을 정식 이름으로 통일, `core/parser.py` 기본 계좌 매핑 규칙 수정 (현재: 해외→"한투일반계좌", 국내→"토스증권기본계좌" 강제)
    - [ ] 파싱 후 알려진 종목명은 `TICKER_MAP` 코드로 덮어쓰는 보정 추가 (LLM 종목코드 오기 방지)
  - 데이터 정리 (transactions)
    - [ ] 계좌명 통합: 토스 일반 ← 토스증권기본계좌 / 한투 일반 ← 한투일반계좌 / 한투 연금저축 ← 한투연금저축 / 카카오 연금저축 ← 카카오페이연금저축계좌
    - [ ] 토스 거래 오배정 수정: id 80, 82, 83, 84 (한투일반계좌 → 토스 일반, 메모상 "토스 화이자")
    - [ ] 종목코드 수정: id 89·90 TIGER 미국배당다우존스 371450 → 458730 / id 91·92 RISE 미국S&P500 458730 → 379780 (오수집 중인 371450 시세 정리 포함)
    - [ ] 토스 일반 해외주식·USD_CASH 5건 `currency` KRW → USD (GOOGL, TSLA, PFE, QQQ, USD_CASH)
    - [ ] 단가 오기 수정 (단가 = 총액 ÷ 수량): id 73(SOL), 88(현대차), 90, 92
    - [ ] 초기 현금 행(`KRW_CASH`/`USD_CASH` BUY) → `DEPOSIT` 으로 변환
  - 현금 계산 B안 전환
    - [ ] `database/repository.py` `get_cash_balances_by_currency` / `get_account_cash_balances` 의 임시 규칙 제거 ("2026-09-01 이전 BUY 무시", "CASH 티커 BUY = 입금")
    - [ ] 계좌별 초기 보유분 매입액만큼 보정 DEPOSIT 입력 (일자 = 계좌별 최초 거래일: UPBIT 2025-01-01, 그 외 2026-05-01, memo "초기 현금 보정")
  - 스냅샷 재구성
    - [ ] 2026-04 스냅샷(daily_snapshots, daily_holding_snapshots 04-01~04-30) 삭제 — 실행 전 확인
    - [ ] 2026-05-01 ~ 현재 스냅샷 전체 백필 및 계좌별 현금·보유 수량 검증
- [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
- [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

## 완료된 직전 작업
- [x] 2026-10-09 운영 DB 일일 백업 스크립트 (`scripts/backup_db.sh`: gzip 덤프·무결성 검사·90일 보관·실패 시 텔레그램 알림, 운영 서버 crontab 매일 04:00 등록)
- [x] 2026-10-09 개발/운영 DB 분리 (`asset_management_dev` + `asset_dev` 계정, 운영 조회 전용 `asset_ro` 계정, 운영 데이터 덤프로 개발 DB 구성)
- [x] 2026-10-09 `schema.sql` 실제 DB와 동기화 (`v_daily_asset_class_summary`, `daily_holding_snapshots`), `daily_prices.close_price` DECIMAL(15,4) 마이그레이션, `init_tables` 주석 뒤 쿼리 스킵 버그 수정
- [x] 2026-10-09 이상징후 감시 기준값 DB화(`detector_settings`), 중복 알림 방지(`anomaly_alert_logs`), 미국주식·코인 24시간 감시
- [x] 2026-10-08 특정 날짜 기준 총자산 및 종목별 세부 스냅샷 저장

## 근미래 작업 (Next Steps)
* [ ] 시차 자산(미국주식 T+1, 펀드 NAV T+1/T+2) 시세 수집 시점 및 일일 스냅샷 정합성 검증
  - 미국 주식/펀드의 직전 유효 시세(Latest Available Price) Fallback 매커니즘 점검
  - 휴장일(공휴일/주말) 및 시차 반영 시 daily_snapshots 평가액 왜곡 방지 로직 확인
* [ ] 텔레그램 커맨드로 이상징후 감시 기준값(detector_settings) 조회/변경 기능 추가
  - 예: `/alert` 현재 기준값 목록, `/alert set kr_prev_close_drop_pct 4.0` 변경 (허용 키·값 범위 검증)
* [ ] 장중 FDR 당일 행 제공 여부 검증 (국내 정규장 / 미국 정규장 중 실제 알림 발생 확인)
* [ ] 계좌 잔액 대사(Reconciliation) 기능: 실제 현금잔액 입력 시 원장과의 차액을 해당 일자 보정 거래(DEPOSIT/WITHDRAW)로 기록
  - 누락된 이자·소액 배당 등을 주기적으로 흡수 (텔레그램 커맨드 또는 CSV 입력)