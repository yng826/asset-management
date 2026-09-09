# TODO.md

## 현재 진행할 작업
- [ ] 세부 스냅샷 기반 계좌별 비중 텍스트 요약 커맨드(`/breakdown`) 구현

## 완료된 직전 작업
- [x] 시간대별 국내/해외 주식 실시간 체결가 연동 및 `/live` 멀티 자산 확장 (자산군별 서브 함수 분리 및 계산용 환율 헬퍼 get_current_fx_rate 연동)
- [x] 이상징후 감시 로직 core/detector 모듈 분리 및 스케줄러 다이어트 리팩토링
- [x] dev_lint.sh fix 옵션 보강 (ruff check --fix --unsafe-fixes 및 format 통합)
- [x] daily_holding_snapshots 테이블 스키마 동기화 및 벌크 UPSERT 메서드 구현 및 백필 연동 완료
- [x] 펀드닥터 수집 로직 내 응답 인코딩 utf-8 명시 및 계산식 보정 완료

## 근미래 작업 (Next Steps)
- [ ] 자산 배분 누적 면적 차트 (`/chart alloc`) 렌더러 구현
- [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
- [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동
