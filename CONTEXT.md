# Portfolio Manager Bot - Context & State

## 1. 프로젝트 개요
* **목적**: 텔레그램 기반 올인원 개인 자산 관리 봇 (국내/해외주식, 가상자산, 펀드, 예금, 현금)
* **주요 기능**:
  - 음성/자연어 거래 입력 파싱 (`Gemini API`) -> DB 원장 기록 (`transactions`)
  - 자산군별 이종 시세 및 환율 자동 수집 -> 실시간 평가액 및 수익률 산출 (`/status`, `/details`)
  - 일별 총자산 스냅샷 집계 (`daily_snapshots`) 및 MariaDB `LAG()` 윈도우 함수 기반 일일 손익/수익률 추산 (`/pnl`)
  - 벤치마크 지수(KOSPI, S&P 500, KOSDAQ, BTC) 대비 정규화 누적 수익률 비교 차트 시각화 및 전송 (`/chart`)
  - 장 마감 시간대별 자동 시세 수집 및 텔레그램 정기 브리핑 (10:30 해외/펀드, 16:00 국내/결산)
* **핵심 기술 스택**:
  - Python 3.10+, MariaDB 10+, python-telegram-bot (v20+ HTML 모드)
  - FinanceDataReader (FDR), pyupbit, BeautifulSoup4, Pandas, Matplotlib
  - Docker & Docker Compose (dev: watchdog/watchmedo 핫리로드, prod: Watchtower & GHCR 무중단 배포)

---

## 2. 개발 및 운영 인프라 환경

### 로컬 개발 환경 (`./scripts/dev.sh`)
* **바인드 마운트**: 호스트 `./` ➔ 컨테이너 `/app` 마운트
* **핫 리로드**: `watchmedo auto-restart --pattern=*.py` 프로세스가 파일 수정을 감지하여 `main.py` 자동 재기동
* **명령어**:
  - 기동: `./scripts/dev.sh up` (또는 `docker compose -f docker-compose.dev.yml up -d --build`)
  - 로그: `./scripts/dev.sh logs`
  - 셸 접속: `./scripts/dev.sh shell`
  - 코드 검사: `./scripts/dev_lint.sh all` (ruff 린트 및 포맷팅 위반 0건 유지 필수)

### 운영 배포 환경 (`./scripts/prod.sh`)
* **CI/CD 파이프라인**: GitHub Actions(`deploy.yml`)에서 `no-cache: true`로 Docker 이미지 빌드 후 GHCR 푸시
* **자동 갱신**: Watchtower가 새 이미지를 감지하여 무중단 자동 교체 기동
* **로깅 및 최적화**: 
  - 호스트 `./logs` ➔ 컨테이너 `/app/logs` 마운트 (`logs/app.log` 영구 보존)
  - `PYTHONUNBUFFERED=1`, `TZ=Asia/Seoul` (한국 시간 동기화)
* **운영 래퍼 스크립트**: `./scripts/prod.sh {logs|app-logs|shell|restart|update|fetch|status}`

---

## 3. 디렉토리 및 모듈 구조

```text
asset-management/
├── bot/                         # 텔레그램 봇 프레젠테이션 계층
│   ├── bot.py                   # Telegram ApplicationBuilder 진입점 및 커맨드 라우팅
│   ├── chart_renderer.py        # Matplotlib 기반 누적 수익률 비교 차트 렌더러 (In-memory BytesIO)
│   └── handlers/
│       ├── voice_handler.py     # 음성/자연어 텍스트 거래 원장 기록 핸들러
│       └── report_handler.py    # 조회 커맨드 (/status, /details, /pnl, /chart, /history, /log)
├── core/                        # 핵심 비즈니스 로직 및 백엔드 도메인
│   ├── calculator.py            # 평가액/원금 집계, 벤치마크 지수 정규화 수익률 파이프라인
│   ├── formatter.py             # 텔레그램 HTML 메시지 포맷팅 및 안전 마진 분할 빌더
│   ├── price_fetcher.py         # 하위 호환 시세 통합 수집 래퍼 (CLI: python -m core.price_fetcher)
│   ├── scheduler.py             # APScheduler 시간대별 자동 시세 수집 및 정기 브리핑
│   ├── parser.py                # Gemini API 기반 자연어/음성 텍스트 구조화 파서
│   ├── fetcher/                 # 자산군별 외부 통신 및 시세 수집 모듈 (독립 분리)
│   │   ├── __init__.py          # 자산군별 수집기 re-export
│   │   ├── kr_stock.py          # 국내 주식/ETF FDR 종가 수집
│   │   ├── us_stock.py          # 미국 주식 종가 수집
│   │   ├── fund.py              # 펀드닥터 HTML 스크래핑 기반 NAV 수집
│   │   ├── crypto.py            # Upbit API 가상자산 종가/현재가 수집
│   │   └── fx.py                # USD/KRW 매매기준율 환율 수집
│   └── valuator/                # 자산군별 개별 평가 로직 (단위 책임 분리)
│       ├── stock.py             # 국내/해외 주식 평가액 계산 (환율 반영)
│       ├── crypto.py            # 가상자산 평가액 계산
│       ├── fund.py              # 펀드 평가액 계산 (NAV / 1000 * 수량)
│       └── deposit.py           # 정기예금 일할 이자 계산
├── database/                    # 영속성 계층 (MariaDB)
│   ├── connection.py            # DB 커넥션 풀
│   ├── repository.py            # CRUD, daily_prices UPSERT, LAG() 기반 일자별 손익 조회
│   └── schema.sql               # transactions, daily_prices, daily_snapshots DDL
├── scripts/                     # 개발 및 운영 자동화 유틸 스크립트
│   ├── dev_lint.sh              # ruff 린트/포맷 통합 검사 래퍼
│   ├── dev.sh / prod.sh         # 개발/운영 컨테이너 관리 셸
│   ├── backfill_daily_prices.py # 과거 시세 백필 (주식, 코인, 지수 1년치)
│   ├── backfill_snapshots.py    # 2026-04-01~ 과거 일별 총자산 스냅샷 백필
│   └── fetch_price.py           # 자산군별 타깃 수동 수집 CLI
├── logs/app.log                 # 봇 런타임 파일 로그 (Rotating/FileHandler)
└── main.py                      # 애플리케이션 통합 진입점 (로깅 초기화, 스케줄러 및 봇 구동)
```

## 4. 텔레그램 메시지 안전 마진 및 UTF-8 분할 전송 안정화

### ✅ Phase 2: 시계열 스냅샷, 이종 자산 확장 및 시각화 (완료)
1. **이종 자산 시세 수집기 확장 및 패키지 모듈화 (`core/fetcher/`)**:
   - 국내주식, 해외주식, 펀드 NAV 스크래핑, 업비트 가상자산, 환율 모듈 격리 구축
2. **과거 시계열 백필 및 스냅샷 엔진**:
   - `daily_prices`: 주요 벤치마크 지수(KS11, US500, KQ11, KRW-BTC) 1년치 백필 완료
   - `daily_snapshots`: 2026-04-01 기준 포트폴리오 총평가액/원금 시계열 백필 완료
3. **성과 분석 및 시각화**:
   - Matplotlib 기반 0% 정규화 누적 수익률 비교 차트 렌더러 구축 (`/chart`)
   - MariaDB `LAG()` 윈도우 함수를 활용한 일자별 손익/일일 수익률 추산 (`/pnl`)
4. **운영 인프라 및 관제**:
   - APScheduler 기반 시간대별(10:30, 16:00 KST) 타깃 수집 및 브리핑 파이프라인 구축
   - 파일 로깅 시스템 및 `/log` 최신 로그 조회 구현, GitHub Actions 무중단 배포 안정화

### 🔜 Phase 3: 고도화 분석, 리스크 지표 및 모니터링 (Next Steps)

1. **계좌·종목 단위 세부 스냅샷 엔진 구축 (`daily_holding_snapshots`)**:
   - `daily_snapshots`(총합) 외에 **(일자, 계좌명, 종목코드, 수량, 종가, 평가액, 원금)** 세부 스냅샷 테이블 신설
   - 일일 배치(`save_today_snapshot`) 및 과거 백필 스크립트 연동
   - 다차원 분석 기반 마련: **국내 개별주 vs 지수 ETF 성과 비교**, **연금/일반 계좌별 비중 추이**, **자산 배분 누적 면적 차트(Stacked Area)**

2. **포트폴리오 리스크 및 성과 분석 고도화**:
   - `daily_snapshots` 기반 MDD(최대 낙폭), 샤프 지수(Sharpe Ratio), 연율화 변동성 산출 모듈 추가
   - 입출금 구간 왜곡을 보정하는 시간가중수익률(TWR) 정밀화

3. **자산 배분 리밸런싱 알림**:
   - 목표 자산 비중(주식/코인/현금/펀드) 설정 및 허용 괴리율 초과 시 주간 알림

4. **프로메테우스 & 그라파나 관제 연동**:
   - 봇 상태 메트릭, 일별 평가액 및 API 응답 레이턴시 대시보드 구축

---

## 5. 🛡️ AI 에이전트 개발 및 리팩토링 안전 원칙 (Agent Rules)

1. **하위 호환성 및 기존 파일 보존 (No Blind Deletion)**:
   - 핵심 진입점(예: `core/price_fetcher.py`)을 임의로 삭제하지 말 것. 신규 패키지로 모듈화할 경우 기존 모듈은 Re-export 또는 경량 래퍼로 보존하여 CLI(`if __name__ == '__main__':`) 및 기존 import 경로를 100% 보장할 것.
   - 핸들러나 포맷터 리팩토링 시 기존 함수(`details_command`, `status_command` 등)를 실수로 누락하거나 덮어쓰지 말 것.
2. **외부 통신 상수 및 정규식 복사 철칙 (No Hallucinated Constants)**:
   - 외부 API URL, 웹 스크래핑 엔드포인트(예: 펀드닥터 URL), 헤더, 정규식, DB 컬럼명은 기억에 의존해 임의로 지어내지 말고 기존 코드/주석에서 1:1로 복사하여 사용할 것.
3. **최소 단위 점진적 수정 (Minimal Blast Radius)**:
   - 과도한 아키텍처 확장(불필요한 `base.py`, `orchestrator.py` 양산 등)을 엄격히 금지함. 작업은 단일 파일 또는 국소 단위로 나누어 진행할 것.
4. **메시지 전송 표준 (HTML Mode)**:
   - 특수문자(`-`, `_` 등) 파싱 에러를 유발하는 `MarkdownV2` 대신 `parse_mode="HTML"`을 기본 표준으로 사용하며, 동적 문자열은 `html.escape()`로 방어할 것.
5. **품질 검사 및 커밋 정책**:
   - 코드 작업 후 반드시 `./scripts/dev_lint.sh all`을 통과하여 위반 사항 0건을 확인할 것.
   - **Git Commit은 사용자가 직접 한국어로 작성하므로 에이전트는 절대 임의 커밋을 수행하지 말 것.**