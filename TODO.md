# TODO.md

## 완료된 직전 작업

- [x] FDR 기반 국내 주식/ETF 당일 종가 수집 및 daily_prices UPSERT 함수 구현 (`core/price_fetcher.py`)
- [x] 영숫자 혼용 6자리 ETF 단축코드(0181L0 등) 수집 처리 및 16개 종목 수집 검증

## 현재 진행할 작업

- [ ] `/status` 명령어 실행 시 daily_prices의 최신 종가를 매핑하여 평가액 및 수익률 출력 로직 연동 (`bot/handlers/report_handler.py`, `core/calculator.py`)
