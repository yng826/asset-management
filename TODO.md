# TODO.md

## 현재 진행할 작업

## 완료된 직전 작업
- [x] 텔레그램 `/chart` 커맨드 연동 (차트 렌더링 및 기간별 조회 기능 구현).
- [x] `core/calculator.py`에 `get_performance_comparison` 함수 및 DB 조회/보간 로직 구현.
- [x] `bot/chart_renderer.py` 차트 렌더러 모듈 구현.
- [x] 일별 총자산 스냅샷 집계 배치 (daily_snapshots 테이블 적재 및 2026-05-01 기준 백필 완료).
- [x] 과거 시세 백필 (주식/ETF 2026-05~, 업비트 가상자산 2025-01~, 주요 벤치마크 지수 1년치 daily_prices 적재).

## 근미래 작업 (Next Steps)
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

