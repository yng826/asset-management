# Portfolio Manager Bot - Context & State

## 1. 프로젝트 개요

- **목적**: 텔레그램 기반 자산 관리 봇 (주식, 펀드, 코인, 예금)
- **주요 기능**: 음성/텍스트 거래 입력 파싱 -> DB 원장 기록 -> 실시간 보유 현황 및 일별 자산 추이/수익률 계산 -> KOSPI/KOSDAQ 지수와 비교 리포트 제공
- **핵심 기술 스택**: Python 3.10+, MariaDB, python-telegram-bot, FinanceDataReader (FDR), Gemini API (거래 텍스트 파싱용)

### 실행 가이드

```bash
# 1) 빌드 + 백그라운드 실행
./scripts/dev.sh up
# 또는
docker compose -f docker-compose.dev.yml up -d --build

# 2) 로그 보기
./scripts/dev.sh logs

# 3) 코드 수정 → 컨테이너에서 watchmedo 가 자동 감지하여 main.py 재기동
#    (별도 명령 불필요)

# 4) 컨테이너 내부 진입
./scripts/dev.sh shell

# 5) 종료
./scripts/dev.sh down
```

### 동작 원리

- 호스트의 `./(소스)` → 컨테이너 `/app` 바인드 마운트 → 호스트에서 `.py` 수정 시 컨테이너에서도 즉시 반영
- 컨테이너 내 `watchmedo auto-restart --pattern=*.py` 가 변경 감지 → 메인 프로세스에 SIGTERM → 5초 대기 → SIGKILL → `python main.py` 재기동
- 디바운스 1초로 짧은 시간 다중 저장 시 1회만 재기동
- `server-bridge` 외부 네트워크에 join 하므로 `DB_HOST=mariadb` 로 컨테이너 이름 DNS 접근

### Dockerfile.dev 빌드 의존성 (확정)

`mariadb` C 커넥터 등 C 확장 의존성 패키지의 wheel 미제공 시 소스 컴파일이 발생하므로,
`apt-get install` 라인에 다음 패키지들을 **반드시** 포함해야 한다:

- `gcc` — C 컴파일러
- `libmariadb-dev` — mariadb C 클라이언트 헤더/스태틱 라이브러리 (mariadb 파이썬 패키지 빌드용)
- `libmariadb3` — mariadb C 런타임 (실행용)
- `python3-dev` — Python.h 헤더 (C 확장 빌드용)

또한 `requirements-dev.txt` 가 `-r requirements.txt` 로 include 하므로,
Dockerfile 의 COPY 구문은 **반드시 두 파일을 함께** 복사해야 한다:

```dockerfile
COPY requirements.txt requirements-dev.txt /app/
```

→ 변경 전처럼 `requirements-dev.txt` 만 단독 COPY 하면
`pip install -r /app/requirements-dev.txt` 단계에서
"No such file or directory: requirements.txt" 오류로 빌드 실패한다.

향후 운영용 Dockerfile 을 별도 작성할 때도 동일한 빌드 의존성을 포함해야 한다.

## 2. 현재 파일 구조

- `config/settings.py`, `constants.py`: 토큰 및 기본 환경설정
- `database/connection.py`, `schema.sql`, `repository.py`: DB 연동 및 CRUD
- `core/parser.py`: 음성/자연어 입력을 매수/매도 데이터로 구조화
- `core/calculator.py`: 보유량 및 평단가 계산 로직(빈 파일)
- `core/price_fetcher.py`: 종가 수집기
- `bot/bot.py`: 봇 진입점. 커맨드 관리
- `bot/handlers/voice_handler.py`: 거래 입력 핸들러 (/buy 등)
- `bot/handlers/report_handler.py`: 조회 핸들러 (/status, /history, /report)
- `main.py`: 진입점

## 3. 현재 구현 완료 상태

- 텔레그램 명령어 `/history`로 최근 거래 내역 조회
- 음성/자연어 파싱을 통한 거래 원장(`transactions`) MariaDB 저장
- 초기 잔고(주식, 펀드, 예금, 외화) 일괄 임포트 완료
- FDR 기반 국내 주식/ETF 당일 종가 수집 및 `daily_prices` UPSERT 파이프라인 구축 (`core/price_fetcher.py`)

## 4. 로드맵 (Roadmap)

### Phase 1: 시세 연동 및 일별 자산 스냅샷

1. **/status 최신 시세 매핑**: `transactions` 잔고와 `daily_prices` 최신 종가를 결합하여 실시간 평가액 및 수익률 출력
2. **일별 총자산 스냅샷 배치 (`daily_snapshots`)**:
   - 날짜별 [보유량 × 당일 종가] 집계 후 `daily_assets (date, total_eval_amount)` 생성
   - 매일 자정 또는 장 마감 후 자동 집계 로직 구성

### Phase 2: 시각화 및 이종 자산 확장

1. **벤치마크 지수(KOSPI/KOSDAQ) 연동**: FDR 기반 지수 일봉 데이터 적재
2. **수익률 비교 차트 시각화**: 내 자산 수익률 곡선 vs 지수 비교 그래프 생성(`matplotlib`) 및 텔레그램 이미지 전송
3. **이종 자산 시세 수집기 확장**: 가상자산(Upbit API), 정기예금(이자 계산), 해외주식(`yfinance`), 펀드 기준가

> **AI 에이전트 개발 지침**:
>
> - 현재는 **Phase 1의 1번(주식/ETF 일일 종가 DB 저장)**에만 집중할 것.
> - 다만, 추후 Phase 2의 지수 비교를 위해 DB 테이블 설계 시 `date` 컬럼과 시계열 조회가 용이한 형태를 유지할 것.

## 5. 지침

- 작업 진행 시 항상 기존 `schema.sql`과 `repository.py`의 구조를 깨지 않고 일관성 있게 확장할 것.
- 불필요하게 대량의 코드를 한 번에 재작성하지 말고, 단계별(함수 단위)로 구현할 것.
