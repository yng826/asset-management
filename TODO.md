## 완료된 직전 작업
- 펀드닥터 수집 로직 내 응답 인코딩을 utf-8로 명시하고 린트 검증 완료.


# TODO.md

## 현재 진행할 작업

## 완료된 직전 작업
- [x] 자산 평가 시 종목별 평가 시도 콘솔 출력 로그 레벨 `logger.debug`로 조정.
- [x] 텔레그램 마크다운 파싱 오류 해결을 위해 HTML 모드 및 `html.escape` 일괄 적용.
- [x] 텔레그램 `/chart` 커맨드 연동 (차트 렌더링 및 기간별 조회 기능 구현).
- [x] `core/calculator.py`에 `get_performance_comparison` 함수 및 DB 조회/보간 로직 구현.
- [x] `bot/chart_renderer.py` 차트 렌더러 모듈 구현.

## 근미래 작업 (Next Steps)
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

