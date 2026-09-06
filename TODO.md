# TODO.md

## 현재 진행할 작업
- [ ] 내 자산 수익률 추이 vs 지수(KOSPI/S&P500/나스닥) 벤치마크 비교 리포트/차트 생성 (matplotlib)

## 완료된 직전 작업
- [x] `core/calculator.py`에 `get_performance_comparison` 함수 및 DB 조회/보간 로직 구현.
- [x] 린트 및 포맷 검사 통과 (`./scripts/dev_lint.sh all`).
- [x] 일별 총자산 스냅샷 집계 배치 (daily_snapshots 테이블 적재 및 2026-05-01 기준 백필 완료)
- [x] 과거 시세 백필 (주식/ETF 2026-05~, 업비트 가상자산 2025-01~, 주요 벤치마크 지수 1년치 daily_prices 적재)
- [x] `get_single_asset_performance` 함수 추가 및 스케줄러 내 `save_today_snapshot` 누락 에러 해결

## 근미래 작업 (Next Steps)
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

