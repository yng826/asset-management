# 자산관리 AI 봇 (asset-management)

텔레그램 기반 자산 관리 봇. 주식, 펀드, 정기예금, 해외주식 보유 종목을 등록하고
일일 종가/환율/NAV 자동 수집 → 평가액/수익률 산출 → `/status`, `/details` 명령 제공.

## 디렉토리 구조

```
asset-management/
├── bot/                     # 텔레그램 봇 핸들러
│   ├── bot.py               # create_bot_app() 진입점
│   └── handlers/
│       ├── voice_handler.py # 음성/텍스트 거래 입력 파싱
│       └── report_handler.py# /status, /details, /history
├── core/                    # 도메인 로직
│   ├── parser.py            # Gemini API 거래 파싱
│   ├── calculator.py        # 평가 오케스트레이터 + DB 조회 + 집계
│   ├── price_fetcher.py     # FDR/펀드닥터 NAV 수집기
│   ├── formatter.py         # 텔레그램 메시지 빌더
│   └── valuator/            # 자산군별 평가 모듈
│       ├── deposit.py       # 정기예금 (일할)
│       ├── fund.py          # 펀드 (NAV/1000 × 수량)
│       ├── stock.py         # 국내주식 + 해외주식 (KRW 환산)
│       └── crypto.py        # 코인 (스텁, 향후 확장)
├── database/                # MariaDB 연동
│   ├── connection.py
│   ├── repository.py
│   └── schema.sql
├── config/                  # .env 로딩, 상수
├── scripts/                 # dev_lint.sh, dev.sh
├── main.py                  # 봇 진입점
├── Dockerfile               # 운영용 (멀티스테이지)
├── Dockerfile.dev           # 개발용 (핫 리로드)
├── docker-compose.yml       # 운영용
├── docker-compose.dev.yml   # 개발용
├── pyproject.toml           # ruff 설정
├── requirements.txt         # 운영 의존성
├── requirements-dev.txt     # 개발 의존성 (watchdog, ruff)
├── .github/workflows/
│   └── deploy.yml           # CI + Build + GHCR Push
└── .env / .env.dev          # 환경변수 (GitHub Secrets 으로도 관리)
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
