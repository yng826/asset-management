# TODO.md

## 완료된 직전 작업

- [x] FDR 기반 국내 주식/ETF 당일 종가 수집 및 daily_prices UPSERT 함수 구현 (`core/price_fetcher.py`)
- [x] 영숫자 혼용 6자리 ETF 단축코드(0181L0 등) 수집 처리 및 16개 종목 수집 검증
- [x] `/status` 명령어 실행 시 daily_prices의 최신 종가를 매핑하여 평가액 및 수익률 출력 로직 연동 (`bot/handlers/report_handler.py`, `core/calculator.py`)
- [x] `core/calculator.py`: holdings ↔ `daily_prices` 가격 매핑 / 계좌·전체 집계 / 텔레그램 청크 빌더
- [x] `bot/handlers/report_handler.py`: `build_status_chunks()` 기반 자동 분할 전송
- [x] 핫 리로드 지원 개발용 Docker 환경 구축 (`scripts/dev.sh`, watchdog)
- [x] `/status` UX 개편 — 한 페이지 요약 + `/details` 신설
  - `core/calculator.py`: `build_status_summary()` 신규 (모바일 1-페이지 압축, 단일 메시지)
  - `bot/handlers/report_handler.py`:
    - `status_command` → `build_status_summary()` 호출 (단일 메시지)
    - `details_command` 신규 → 기존 `build_status_chunks()` 호출 (다중 청크, 상세 종목 리스트)
  - `bot/bot.py`: `/details` 핸들러 등록 + `/start` 메시지에 명령어 안내 갱신
  - 검증: `/status` 945자 단일 메시지, `/details` 3개 청크 (2,344/2,358/745), 모두 4,096자 한도 내
- [x] 정기예금 자산의 평가액 계산 로직 구현 (`core/calculator.py`)
  - `is_deposit(ticker_name, ticker_code)`: ticker_name='정기예금'/'예금' + code='이율|시작일|만기일' 패턴 식별
  - `parse_deposit_metadata(ticker_code)`: '4.42|2023-01-02|2028-01-02' → {annual_rate, start_date, end_date}
  - `calculate_deposit_valuation(principal, rate, start, end, today)`: 경과일수 × (rate/100) / 365 일할 계산, 만기 경과 시 상한 제한
  - `enrich_holdings_with_prices()`: 우선순위 (1) deposit 일할 계산 → (2) market → (3) fallback. price_source='deposit' 추가
  - `_render_lines()`: price_source 별 마크 분기 (`[정기예금·일할]` / `[예수금/미수집]` / 없음)
  - 검증: 보유 정기예금 (3,975,427원 × 4.42% × 1342/365) = 세전 이자 646,049원, 평가액 4,621,476원, 수익률 +16.25%
  - /status 출력: 한투 IRP 손익 0% → 🔺 +9.72% 로 자동 반영, 전체 포트폴리오 손익 +65.5M → +66.1M
  - 시세 수집 없이 순수 산술식(원금 × 이율 × 경과일수 / 365)으로 평가액 자동 반영. (위 항목으로 완료)
- [x] 해외주식(직투) 시세 및 환율 연동
- [x] 코드 품질 자동 검사 프로세스 수립 (ruff 도입)
  - `requirements-dev.txt`: `ruff>=0.6` 추가
  - `pyproject.toml` 신규: `[tool.ruff]` + `[tool.ruff.lint]` 통합 설정
    - 룰: E/W(PEP8), F(pyflakes, 데드코드 차단), I(isort), B(bugbear), UP(pyupgrade), SIM(simplify), C4(comprehensions)
    - 무시: E501(line-length 위임), B008(default-arg 함수), SIM108(삼항 강제 비활성)
    - per-file-ignores: `__init__.py`(F401), `scripts/*.py`(E402/E501), `tests/*.py`(E501/B011)
  - `scripts/dev_lint.sh` 신규: `check / fix / format / format-check / all` 5개 서브커맨드
  - `Dockerfile.dev`: ruff 자동 포함 (requirements-dev.txt 경유), docstring 만 보강
  - 현재 코드 35개 위반 발견 → 26개 자동 수정 + 9개 수동 정리 → 전수 통과 (lint+format 모두 0)
  - 회귀 테스트: /status 945자 (해시 97a93c7ecbbd baseline 일치), /details 3개 청크, enriched 39개 중복 0

## 현재 진행할 작업

- 금투협(KOFIA) 또는 네이버 펀드 페이지를 통한 펀드 기준가(NAV) 주기적 갱신 파이프라인 구축.

---

## 다음 단계 (백로그)

- 운영용 Dockerfile / docker-compose.yml 분리 (핫 리로드 OFF, 멀티스테이지 빌드)
- CI/CD: GitHub Actions 로 푸시 시 자동 빌드 + (선택) 레지스트리 배포
- Prometheus / Grafana 메트릭 노출 (봇 헬스체크, 메시지 처리 latency)
