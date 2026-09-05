# TODO.md

## 현재 진행할 작업

- [ ] 주요 벤치마크 지수 및 환율 수집 기능 추가 (`daily_prices` 연동)
  - 대상:
    - 국내: 코스피(`KS11`), 코스닥(`KQ11`)
    - 해외: S&P500(`US500`), 나스닥(`IXIC`), 다우존스(`DJI`)
    - 환율: 원/달러(`USD/KRW`)
  - `core/price_fetcher.py`: FDR 기반 지수/환율 수집 함수 구현 및 일일 스케줄러 연동
  - `scripts/backfill_indices.py`: 최근 1년치 지수/환율 일봉 데이터 일괄 적재 스크립트 작성
  - 기존 `holdings` 기반 평가 로직(`core/valuator/*`, `core/calculator.py`)과의 격리 검증

## 완료된 직전 작업

- [x] APScheduler 기반 정기 알림 스케줄러 도입 (`core/scheduler.py`, 평일 10:30, 16:00, 토 10:00)
- [x] 운영 배포 인프라 및 CI/CD 구축 (운영용 멀티스테이지 Dockerfile, docker-compose, GitHub Actions)
- [x] 평가 로직 분산 리팩토링 (`core/valuator/{deposit, fund, stock, crypto}.py`)
- [x] 펀드닥터 HTML 크롤러 기반 퇴직연금 펀드 기준가(NAV) 수집 및 평가 연동

## 근미래 작업 (Next Steps)

- [ ] 일별 총자산 스냅샷 집계 배치 (`daily_snapshots` 테이블 적재)
- [ ] 내 자산 수익률 추이 vs 지수(KOSPI/S&P500/나스닥) 벤치마크 비교 리포트/차트 생성 (`matplotlib`)
- [ ] 가상자산(Upbit API) 시세 수집기 확장 (`core/valuator/crypto.py` 실구현)
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

---

> 상세 과거 작업 내역은 `docs/archive/todo_archive.md` 참조
