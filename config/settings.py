import os
from pathlib import Path

from dotenv import load_dotenv

# 로드할 env 파일: 기본 .env(운영), 개발은 ENV_FILE=.env.dev (개발 컨테이너·호스트에서 스크립트 실행 시)
# 상대 경로는 프로젝트 루트 기준. 이미 설정된 환경변수(compose env_file 등)는 덮어쓰지 않음
ENV_FILE = os.getenv("ENV_FILE", ".env")
ENV_FILE_PATH = (
    Path(ENV_FILE) if Path(ENV_FILE).is_absolute() else Path(__file__).resolve().parent.parent / ENV_FILE
)
load_dotenv(ENV_FILE_PATH)

# Database
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "asset_management")

# 개발 env 인데 운영 DB를 가리키면 즉시 중단 (개발 봇·스크립트의 운영 데이터 오염 방지)
if ENV_FILE.endswith(".dev") and not DB_NAME.endswith("_dev"):
    raise RuntimeError(
        f"ENV_FILE={ENV_FILE} 인데 DB_NAME={DB_NAME} (운영 DB) 입니다. .env.dev 의 DB_NAME 을 확인하세요."
    )

# 시세 수집 이관 스위치: 1 이면 수집·스냅샷은 k3s 수집기(collector, deploy/k3s)가 맡고 봇은 발송만 수행
# 기본값 0 = 기존과 동일하게 봇 APScheduler 가 수집까지 수행
COLLECTOR_EXTERNAL = os.getenv("COLLECTOR_EXTERNAL", "0").strip().lower() in {"1", "true", "yes"}

# 시세 저장 테이블: 수집기 병행(섀도) 운영 중에는 daily_prices_k3s 에 따로 쌓아 기존 시세와 비교
# 봇은 설정하지 않음 (기본 daily_prices). SQL 에 들어가므로 허용 목록 외 값은 기동 중단
PRICE_TABLE = os.getenv("PRICE_TABLE", "daily_prices").strip()
if PRICE_TABLE not in {"daily_prices", "daily_prices_k3s"}:
    raise RuntimeError(f"PRICE_TABLE={PRICE_TABLE} 는 허용되지 않습니다 (daily_prices / daily_prices_k3s)")
SHADOW_MODE = PRICE_TABLE != "daily_prices"

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_raw_admin_id = os.getenv("ADMIN_USER_ID", "0").strip("'\"")
ADMIN_USER_ID = int(_raw_admin_id) if _raw_admin_id.isdigit() else 0


# 공공데이터포털(금융위원회 증권정보 Open API) 인증키
# - 펀드 표준코드(srtnCd ↔ asoStdCd) 매핑 조회에 사용
# - 발급처: https://www.data.go.kr
DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY")
