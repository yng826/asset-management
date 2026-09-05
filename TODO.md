# TODO.md

## 완료된 직전 작업

- [x] FDR 기반 국내 주식/ETF 당일 종가 수집 및 daily_prices UPSERT 함수 구현 (`core/price_fetcher.py`)
- [x] 영숫자 혼용 6자리 ETF 단축코드(0181L0 등) 수집 처리 및 16개 종목 수집 검증
- [x] `/status` 명령어 실행 시 daily_prices의 최신 종가를 매핑하여 평가액 및 수익률 출력 로직 연동 (`bot/handlers/report_handler.py`, `core/calculator.py`)
- [x] `core/calculator.py`: holdings ↔ `daily_prices` 가격 매핑 / 계좌·전체 집계 / 텔레그램 청크 빌더
- [x] `bot/handlers/report_handler.py`: `build_status_chunks()` 기반 자동 분할 전송
- [x] 핫 리로드 지원 개발용 Docker 환경 구축 (`scripts/dev.sh`, watchdog)

## 현재 진행할 작업

- /status 명령의 간소화.
  - 현재의 /status는 /details 라는 새 명령으로 이전
  - 새 /status 는 '한 페이지 요약 리포트' 정도로 짧게(전일대비, 종합손익, 수익률등)

---

## 다음 단계 (백로그)

- 운영용 Dockerfile / docker-compose.yml 분리 (핫 리로드 OFF, 멀티스테이지 빌드)
- CI/CD: GitHub Actions 로 푸시 시 자동 빌드 + (선택) 레지스트리 배포
- Prometheus / Grafana 메트릭 노출 (봇 헬스체크, 메시지 처리 latency)
