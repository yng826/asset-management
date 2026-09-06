# TODO.md

## 현재 진행할 작업
`좋은 전략입니다. 무료 티어의 API 제한(RPM)을 고려하여, **핵심 평가 엔진인 `core/calculator.py`를 확장하여 개별 종목의 수익률을 계산하는 함수를 추가**하는 것부터 시작하겠습니다.

이 함수는 기존의 `enrich_holdings_with_prices` 로직을 활용하므로 코드 중복을 최소화할 수 있습니다.`

### 계획: 개별 종목 수익률 계산 함수 추가
1.  **`core/calculator.py`에 `get_single_asset_performance` 함수 추가**:
    - 매개변수: `ticker_code`(또는 종목명), `holdings_list`, `price_map`, `fx_rate`
    - 로직: 
        1. 해당 종목의 보유량 및 매수 정보를 찾음.
        2. `valuator` 체인을 통해 현재가를 적용하여 평가액 산출.
        3. 수익금 및 수익률(`_safe_pnl_rate` 활용) 계산.
        4. 결과 리턴.

---

### 실행 단계 (현재 모드: Plan)

1.  **파일 읽기**: `core/calculator.py`와 `core/valuator/stock.py`의 구조를 다시 한번 확인하여 일관성 있게 구현할 수 있는지 검토합니다.
2.  **함수 정의**: `core/calculator.py` 하단에 새로운 계산 함수를 구현합니다.
3.  **검증**: 작성된 코드가 기존의 데이터 구조와 잘 맞는지 확인합니다.

---

### 1단계: 검토를 위한 파일 조회
정확한 데이터 구조를 파악하기 위해 `core/valuator/stock.py`를 읽어보겠습니다. (이미 `core/calculator.py`는 읽었습니다.)

발생할 수 있는 종목명과 티커 간의 불일치 문제를 해결하기 위해, 현재 `AssetRepository`가 종목명을 어떻게 저장하는지도 짧게 확인할 필요가 있습니다.

`read_files`를 사용하여 다음 두 파일을 확인하겠습니다.
- `/home/hyuk/docker-server/asset-management/core/valuator/stock.py`
- `/home/hyuk/docker-server/asset-management/database/repository.py` (간략히)

이후 함수 구현 단계로 넘어갑니다. **바로 실행하시겠습니까?**

## 완료된 직전 작업

- [x] 가상자산(Upbit API) 시세 수집기 및 평가 연동 (`core/price_fetcher.py`에 Upbit API 연동, `core/valuator/crypto.py`에 평가 로직 구현)
- [x] 주요 벤치마크 지수 및 환율 수집 기능 추가 (`core/price_fetcher.py`, `scripts/backfill_indices.py`, `core/scheduler.py` 연동)

## 근미래 작업 (Next Steps)

- [ ] 내 자산 수익률 추이 vs 지수(KOSPI/S&P500/나스닥) 벤치마크 비교 리포트/차트 생성 (`matplotlib`
- [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동

---

> 상세 과거 작업 내역은 `docs/archive/todo_archive.md` 참조
