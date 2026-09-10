# TODO.md

## 현재 진행할 작업
- [ ] 자산 배분 누적 면적 차트 (`/chart alloc`) 렌더러 구현
- [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
- [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

## 완료된 직전 작업
- [x] 텔레그램 `/breakdown` 커맨드 구현 (자산군별 비중 리포트 연동)
- [x] 정규 배치 시작 시 과거 누락분 자동 감지 및 자가 치유(Auto-healing) 루틴 구축
- [x] 모닝 브리핑 수집 시간대 최적화 및 08:30 조기 수집 검증용 사전 프로브(Probe) 스케줄 추가
- [x] daily_holding_snapshots 단가·평가액 인터페이스 불일치(current_price/eval_amount) 수정 및 1원 단위 정합성 검증
- [x] 미국 주식 과거 시세(2026-09-08~09) 긴급 백필 및 daily_prices 영속성 동기화

## 근미래 작업 (Next Steps)
- [ ] ...
