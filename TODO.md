# TODO.md

## 현재 진행할 작업
- [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
- [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

## 완료된 직전 작업
- [v] 자산 배분 누적 면적 차트 (`/chart alloc`) 자산배분 재정리
- [x] 신규 DB 뷰 `v_latest_asset_breakdown` 기반으로 `/breakdown` 조회 로직 최적화
- [x] `/breakdown` 리포트에 어제 대비 자산군별 변동 내역(금액/등락률) 추가 및 DB 로직 확장
- [x] 텔레그램 `/breakdown` 커맨드 구현 (자산군별 비중 리포트 연동)
- [x] 정규 배치 시작 시 과거 누락분 자동 감지 및 자가 치유(Auto-healing) 루틴 구축
- [x] 모닝 브리핑 수집 시간대 최적화 및 08:30 조기 수집 검증용 사전 프로브(Probe) 스케줄 추가

## 근미래 작업 (Next Steps)
- [ ] ...
