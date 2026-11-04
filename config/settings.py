import os
import re
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
PRICE_TABLES = {"daily_prices", "daily_prices_k3s", "daily_prices_broker"}
if PRICE_TABLE not in PRICE_TABLES:
    raise RuntimeError(f"PRICE_TABLE={PRICE_TABLE} 는 허용되지 않습니다 ({' / '.join(sorted(PRICE_TABLES))})")
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

# 증권사 Open API (조회 전용, core/broker). 값이 없으면 해당 증권사는 비활성
# 토스증권: client_id/secret 은 개발은 .env.dev, 운영은 k3s Secret(collector-env)에만 둔다 (저장소 커밋 금지)
TOSS_CLIENT_ID = os.getenv("TOSS_CLIENT_ID", "").strip()
TOSS_CLIENT_SECRET = os.getenv("TOSS_CLIENT_SECRET", "").strip()
# 한국투자증권: 앱키/시크릿과 계좌 목록("한투 일반=12345678-01,한투 ISA=…")도 운영은 k3s Secret 에만 둔다
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()
KIS_ACCOUNTS = os.getenv("KIS_ACCOUNTS", "").strip()


def _collect_kis_blocks() -> dict[int, dict[str, str]]:
    """계좌별 설정 KIS_<번호>_{NAME,ACCOUNT,APP_KEY,APP_SECRET} 를 번호순으로 모은다 (한투 앱키는 계좌마다 따로 발급)."""
    blocks: dict[int, dict[str, str]] = {}
    for key, value in os.environ.items():
        m = re.fullmatch(r"KIS_(\d+)_(NAME|ACCOUNT|APP_KEY|APP_SECRET)", key)
        if m:
            blocks.setdefault(int(m.group(1)), {})[m.group(2)] = value.strip()
    return dict(sorted(blocks.items()))


KIS_BLOCKS = _collect_kis_blocks()
# 증권사 시세 섀도(broker-prices 작업, PRICE_TABLE=daily_prices_broker)에서 쓸 증권사
PRICE_BROKER = os.getenv("PRICE_BROKER", "toss").strip()

# 체결 가져오기(trades 작업, 3단계): 도입일(YYYY-MM-DD) 이전 체결은 저장하지 않는다. 비어 있으면 작업이 중단된다
EXEC_SINCE = os.getenv("EXEC_SINCE", "").strip()
# 체결을 가져올 증권사 (쉼표 구분). 한투는 4b 에서 executions 구현 후 추가
EXEC_BROKERS = [b.strip() for b in os.getenv("EXEC_BROKERS", "toss").split(",") if b.strip()]
# 오늘 기준 몇 일 전까지 겹쳐 조회할지 (중복 키가 흡수하므로 한 번 놓쳐도 다음 실행이 메운다)
EXEC_LOOKBACK_DAYS = int(os.getenv("EXEC_LOOKBACK_DAYS", "3"))
