# TODO.md

## 현재 진행할 작업
- [ ] 주말 작업: 계좌명 파편화 정리 (transactions UPDATE) 및 시작일(2026-04-01) 기준 계좌별 초기 현금 보정 DEPOSIT 등록
- [ ] 보정 후 4월~현재 전구간 일별 스냅샷 전체 백필 (backfill_snapshots 2026-04-01) 및 실잔액 검증
- [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
- [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

## 완료된 직전 작업
- [x] 일별 스냅샷(daily_snapshots) 순현금 집계 로직 정상화 (가상 CASH 티커 필터링 제거 및 거래 원장 기반 get_cash_balances_by_currency 연동)
- [x] 자연어 거래 파서(TransactionParser) 다건 상대일자 상속, 기본 계좌 자동 매핑, 통화(currency) 컬럼 지원 및 다건 거래(텍스트/음성) 연동

## 근미래 작업 (Next Steps)
* [ ] 시차 자산(미국주식 T+1, 펀드 NAV T+1/T+2) 시세 수집 시점 및 일일 스냅샷 정합성 검증
  - 미국 주식/펀드의 직전 유효 시세(Latest Available Price) Fallback 매커니즘 점검
  - 휴장일(공휴일/주말) 및 시차 반영 시 daily_snapshots 평가액 왜곡 방지 로직 확인