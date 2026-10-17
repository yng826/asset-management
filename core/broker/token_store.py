"""증권사 액세스 토큰 DB 캐시 (api_tokens, 증권사별 1행)

토스는 client 당 유효 토큰이 1개라 두 프로세스가 동시에 발급하면 먼저 받은 쪽 토큰이 죽는다.
그래서 행을 FOR UPDATE 로 잠근 뒤 다시 확인하고, 그래도 만료 임박일 때만 발급한다.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from .base import BrokerError

logger = logging.getLogger(__name__)

REFRESH_MARGIN = timedelta(hours=1)  # 만료 1시간 전부터 새로 발급

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS api_tokens (
    broker VARCHAR(20) NOT NULL PRIMARY KEY,
    access_token TEXT NOT NULL,
    expires_at DATETIME NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
)
"""


def ensure_table() -> None:
    from database.connection import execute

    execute(_CREATE_SQL)


def _usable(expires_at: datetime) -> bool:
    return expires_at - datetime.now() > REFRESH_MARGIN


def get_token(broker: str, issue_fn: Callable[[], tuple[str, int]]) -> str:
    """캐시된 토큰 반환. 없거나 만료 임박이면 issue_fn() -> (access_token, expires_in 초)로 발급·저장."""
    from database.connection import get_connection

    ensure_table()
    conn = get_connection()
    if not conn:
        raise BrokerError("DB 연결 실패 (토큰 캐시 사용 불가)")
    try:
        conn.autocommit = False
        cur = conn.cursor()
        # 행이 아직 없으면 잠글 대상이 없으므로 만료된 자리표시 행을 먼저 만든다
        cur.execute(
            "INSERT IGNORE INTO api_tokens (broker, access_token, expires_at) VALUES (?, '', '1970-01-01')",
            (broker,),
        )
        cur.execute("SELECT access_token, expires_at FROM api_tokens WHERE broker = ? FOR UPDATE", (broker,))
        token, expires_at = cur.fetchone()
        if token and _usable(expires_at):
            conn.commit()
            return token
        token, expires_in = issue_fn()
        new_expiry = datetime.now() + timedelta(seconds=expires_in)
        cur.execute(
            "UPDATE api_tokens SET access_token = ?, expires_at = ? WHERE broker = ?",
            (token, new_expiry, broker),
        )
        conn.commit()
        logger.info(f"{broker} 토큰 신규 발급 (만료 {new_expiry:%Y-%m-%d %H:%M})")
        return token
    except BrokerError:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise BrokerError(f"{broker} 토큰 저장 실패: {e}") from e
    finally:
        conn.close()


def invalidate(broker: str) -> None:
    """401 을 받은 토큰을 만료 처리 (다음 get_token 이 재발급)."""
    from database.connection import execute

    execute("UPDATE api_tokens SET expires_at = '1970-01-01' WHERE broker = ?", (broker,))
