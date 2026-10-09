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

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_raw_admin_id = os.getenv("ADMIN_USER_ID", "0").strip("'\"")
ADMIN_USER_ID = int(_raw_admin_id) if _raw_admin_id.isdigit() else 0


# 공공데이터포털(금융위원회 증권정보 Open API) 인증키
# - 펀드 표준코드(srtnCd ↔ asoStdCd) 매핑 조회에 사용
# - 발급처: https://www.data.go.kr
DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY")
