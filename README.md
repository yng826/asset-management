# 자산관리 AI 봇 (asset-management)

텔레그램 기반 자산 관리 봇. 주식, 펀드, 정기예금, 해외주식 보유 종목을 등록하고
일일 종가/환율/NAV 자동 수집 → 평가액/수익률 산출 → `/status`, `/details` 명령 제공.

## 디렉토리 구조

```
asset-management/
├── bot/                     # 텔레그램 봇 프레젠테이션 계층
│   ├── bot.py               # create_bot_app() 및 커맨드 라우팅 진입점
│   ├── chart_renderer.py    # Matplotlib 기반 누적 수익률 비교 차트 렌더러
│   └── handlers/
│       ├── voice_handler.py # 음성/텍스트 거래 입력 파싱
│       └── report_handler.py# /status, /details, /pnl, /chart, /history, /log
├── core/                    # 도메인 로직 및 비즈니스 엔진
│   ├── parser.py            # Gemini API 거래 파싱
│   ├── calculator.py        # 평가 오케스트레이터 + DB 조회 + 집계
│   ├── formatter.py         # 텔레그램 HTML 메시지 포맷팅 및 안전 마진 분할 빌더
│   ├── price_fetcher.py     # 하위 호환 시세 통합 수집 래퍼 (CLI 지원)
│   ├── scheduler.py         # APScheduler 기반 정기 브리핑 및 이상징후 감시 오케스트레이션
│   ├── fetcher/             # 자산군별 시세/환율 수집 모듈 (외부 API/스크래핑 격리)
│   │   ├── __init__.py      # 수집 함수 re-export
│   │   ├── kr_stock.py      # 국내 주식/ETF 수집 (FDR)
│   │   ├── us_stock.py      # 미국 주식 종가 수집
│   │   ├── fund.py          # 펀드닥터 HTML 스크래핑 기반 NAV 수집
│   │   ├── crypto.py        # Upbit API 가상자산 시세 수집
│   │   └── fx.py            # USD/KRW 환율 수집
│   ├── detector/            # [신규] 장전/장중/시간외 이상징후 감시 체커
│   │   ├── __init__.py      # 감시 함수 re-export
│   │   └── anomaly.py       # 갭출발, 장중 고점 낙폭, 시간외 급변 판정 로직
│   └── valuator/            # 자산군별 평가 모듈 (단위 책임 분리)
│       ├── deposit.py       # 정기예금 (일할)
│       ├── fund.py          # 펀드 (NAV/1000 × 수량)
│       ├── stock.py         # 국내주식 + 해외주식 (KRW 환산)
│       └── crypto.py        # 가상자산 평가
├── database/                # MariaDB 영속성 계층
│   ├── connection.py        # DB 커넥션 풀
│   ├── repository.py        # CRUD, 일일 스냅샷 적재, LAG() 손익 조회, 배치 감사 로그
│   └── schema.sql           # transactions, daily_prices, daily_snapshots DDL
├── config/                  # 환경변수 로딩 및 설정 상수
├── scripts/                 # 개발, 린트 및 운영 관리 스크립트
│   ├── dev.sh               # 개발 컨테이너 제어
│   ├── prod.sh              # 운영 컨테이너 관리 (로그, 업데이트, 수동 수집)
│   ├── dev_lint.sh          # ruff 린트/포맷 통합 검사 및 자동 수정(fix)
│   ├── fetch_price.py       # 자산군별 단독 수동 수집 CLI
│   ├── backfill_daily_prices.py # 과거 시세 백필
│   └── backfill_snapshots.py    # 과거 일별 총자산 스냅샷 백필
├── logs/app.log             # 봇 런타임 파일 로그
└── main.py                  # 통합 진입점 (로깅 초기화, 스케줄러 및 봇 구동)
```

## 개발 환경 (docker-compose.dev.yml)

```bash
# 빌드 + 백그라운드 실행
./scripts/dev.sh up

# 로그 보기
./scripts/dev.sh logs

# lint 검사
./scripts/dev_lint.sh all
```

소스는 볼륨 마운트되어 watchmedo 가 자동 재기동. 핫 리로드.

## 운영 배포 (docker-compose.yml + GitHub Actions)

### 아키텍처
```
[GitHub push] → [GitHub Actions]
                       │
                       ├─ 1) lint (ruff)
                       └─ 2) Build multi-stage Dockerfile
                                  │
                                  └─ 3) Push to ghcr.io
                                            │
                                  [운영 서버 수동 배포]
                                            │
                                            ├─ docker compose pull
                                            └─ docker compose up -d
```

> **Watchtower 미사용** (요구사항) — GitHub Actions 가 빌드/푸시 담당, 운영 서버는 `docker compose pull && up -d` 한 줄로 배포.

### 1단계: GitHub Secrets 등록 (1회만)

GitHub 리포지토리 → **Settings → Secrets and variables → Actions** 에서 다음 시크릿 추가:

| 시크릿 이름 | 값 (예시) | 비고 |
|------------|----------|------|
| (자동) | `GITHUB_TOKEN` | GitHub Actions 자동 제공 (GHCR 푸시 권한) |
| (없음) | - | DB_PASS 등은 **이미지 미포함** → 호스트 .env 또는 docker compose env_file 로 주입 |

### 2단계: 첫 푸시 (릴리스)

```bash
git add .
git commit -m "feat: 운영 배포 인프라 (Dockerfile, docker-compose.yml, GH Actions)"
git push origin main
```

GitHub Actions 가 자동으로:
1. ruff lint + format 검사 (실패 시 중단)
2. Dockerfile 멀티스테이지 빌드
3. `ghcr.io/<owner>/asset-management-bot:latest` + `:sha-<short>` 푸시

### 3단계: 운영 서버 초기 배포 (1회만)

```bash
# 1) 코드 클론
git clone <repo-url> /opt/asset-management
cd /opt/asset-management

# 2) .env 작성 (호스트에 저장, GitHub 에는 커밋 X)
cat > .env <<'EOF'
DB_HOST=mariadb
DB_PORT=3306
DB_USER=kodi
DB_PASSWORD=kodi1234
DB_NAME=asset_management
GEMINI_API_KEY=...
TELEGRAM_BOT_TOKEN=...
DATA_GO_KR_API_KEY=...
DATA_GO_KR_FUND_FETCH_ENABLED=0
