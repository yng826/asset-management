# TODO.md

## 현재 진행할 작업
- [ ] 가상자산(Upbit API) 시세 수집기 및 평가 연동
  - `core/valuator/crypto.py`: Upbit 보유 코인 평가 로직 실구현
  - `core/price_fetcher.py`: Upbit Public Ticker API 연동 (KRW 마켓 현재가 수집)
  - `/status`, `/details` 출력 시 가상자산 정상 평가액 반영 확인

## 완료된 직전 작업

- [x] 주요 벤치마크 지수 및 환율 수집 기능 추가 (`core/price_fetcher.py`, `scripts/backfill_indices.py`, `core/scheduler.py` 연동)

## 근미래 작업 (Next Steps)

- [ ] 일별 총자산 스냅샷 집계 배치 (`daily_snapshots` 테이블 적재)
- [ ] 내 자산 수익률 추이 vs 지수(KOSPI/S&P500/나스닥) 벤치마크 비교 리포트/차트 생성 (`matplotlib`
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

---

> 상세 과거 작업 내역은 `docs/archive/todo_archive.md` 참조
