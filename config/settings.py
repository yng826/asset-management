import os

from dotenv import load_dotenv

load_dotenv()

# Database
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "asset_management")

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))


# 공공데이터포털(금융위원회 증권정보 Open API) 인증키
# - 펀드 표준코드(srtnCd ↔ asoStdCd) 매핑 조회에 사용
# - 발급처: https://www.data.go.kr
DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY")
