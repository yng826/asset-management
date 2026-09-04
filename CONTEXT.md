# Portfolio Manager Bot - Context & State

## 1. 프로젝트 개요

- **목적**: 텔레그램 기반 자산 관리 봇 (주식, 펀드, 코인, 예금)
- **주요 기능**: 음성/텍스트 거래 입력 파싱 -> DB 원장 기록 -> 실시간 보유 현황 및 일별 자산 추이/수익률 계산 -> KOSPI/KOSDAQ 지수와 비교 리포트 제공
- **핵심 기술 스택**: Python 3.10+, MariaDB, python-telegram-bot, FinanceDataReader (FDR), Gemini API (거래 텍스트 파싱용)

## 2. 현재 파일 구조

- `config/settings.py`, `constants.py`: 토큰 및 기본 환경설정
- `database/connection.py`, `schema.sql`, `repository.py`: DB 연동 및 CRUD
- `core/parser.py`: 음성/자연어 입력을 매수/매도 데이터로 구조화
- `core/calculator.py`: 보유량 및 평단가 계산 로직
- `core/price_fetcher.py`: 종가 수집기
- `bot/handlers/voice_handler.py`: 거래 입력 핸들러 (/buy 등)
- `bot/handlers/report_handler.py`: 조회 핸들러 (/status, /history, /report)
- `main.py`: 봇 진입점

## 3. 현재 구현 완료 상태

- 텔레그램 명령어 `/history`로 최근 거래 10건 조회 성공
- 거래 원장 기반 `/status`로 보유 종목, 수량, 평단가, 총 평가액 출력 성공
- 음성/자연어 파싱을 통한 거래 내역 MariaDB 저장 완료

## 4. 진행할 다음 작업 (Roadmap & TODO)

### [Phase 1: 현재 집중 작업] 종가 수집 및 일별 스냅샷 파이프라인

1. **[진행중] 종목별 일일 종가 수집기 구축 (주식/ETF 우선)**
   - 보유 자산 중 국내 주식/ETF 대상 FinanceDataReader 기반 당일 종가 수집
   - `daily_prices (symbol, date, close_price)` 테이블에 중복 없이 upsert 처리
   - _(코인, 펀드, 예금 등 이종 자산 확장은 주식 파이프라인 안정화 후 순차 확장)_

2. **[예정] 일별 총자산 스냅샷 배치 (`daily_snapshots`)**
   - 날짜별 [보유량 x 해당일 종가]를 집계하여 `daily_assets (date, total_eval_amount)` 생성
   - 매일 자정 또는 장 마감 후 자동 집계 로직 구성

---

### [Phase 2: 백로그 - 당장 구현하지 않음, 아키텍처만 고려]

- **가상자산/예금/펀드 가격 파서 확장**: Upbit API 및 고정 이자 계산 로직
- **벤치마크 지수(KOSPI/KOSDAQ) 연동**: FDR을 통해 지수 일봉을 긁어와 내 수익률 곡선과 매핑
- **상대 수익률 시각화**: matplotlib을 이용해 텔레그램으로 비교 그래프 전송

> **AI 에이전트 개발 지침**:
>
> - 현재는 **Phase 1의 1번(주식/ETF 일일 종가 DB 저장)**에만 집중할 것.
> - 다만, 추후 Phase 2의 지수 비교를 위해 DB 테이블 설계 시 `date` 컬럼과 시계열 조회가 용이한 형태를 유지할 것.

## 5. 지침

- 작업 진행 시 항상 기존 `schema.sql`과 `repository.py`의 구조를 깨지 않고 일관성 있게 확장할 것.
- 불필요하게 대량의 코드를 한 번에 재작성하지 말고, 단계별(함수 단위)로 구현할 것.
