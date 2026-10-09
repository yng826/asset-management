# TODO.md

## 현재 진행할 작업
- [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
- [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

## 완료된 직전 작업
- [x] 2026-10-09 거래 원장 정합성 정리 및 현금 계산 정식 장부 방식 전환 (개발·운영 DB 적용 완료)
  - 재발 방지: `ASSET_MAP` 정식 계좌명 통일 + `ACCOUNT_ALIASES`/`DEFAULT_ACCOUNT`("토스 일반"), 파서 출력 보정(`_normalize_items`: 계좌명 정식화, `TICKER_MAP` 코드 강제, 단가 재계산)
  - 데이터 정리(`scripts/migrate_ledger_cleanup.py`, 백업 `transactions_backup_20261009`): 계좌명 통합, 토스 거래 오배정(id 80·82·83·84), 종목코드(id 89~92), USD 통화 5건, 단가 4건, 초기 현금 행 BUY→DEPOSIT
  - 현금 계산 임시 규칙 제거 + "초기 현금 보정" DEPOSIT 11건, 실제 잔액 대비 "잔액 대사 보정" 10건(2026-10-09), 카카오 일반 관리 대상 제외
  - 2026-05-01 ~ 10-09 스냅샷 재계산 (04월 이전 스냅샷은 부정확함을 감안하고 보존, 오수집된 371450 시세 1행 잔존)
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