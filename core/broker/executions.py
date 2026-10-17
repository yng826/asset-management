"""증권사 체결 → broker_executions(대기 테이블) 저장 (3단계). 원장(transactions) 기록은 봇의 승인 버튼만 한다."""

import logging
from datetime import date, timedelta

from config.settings import EXEC_BROKERS, EXEC_LOOKBACK_DAYS, EXEC_SINCE
from core.broker import BrokerError, get_broker
from core.broker.base import Execution
from database.connection import execute, execute_many, fetch_all

logger = logging.getLogger(__name__)

# database/schema.sql 의 broker_executions 와 동일 DDL (봇이 먼저 만들지 않았을 때 수집기가 만들 수 있도록)
_DDL = """
CREATE TABLE IF NOT EXISTS broker_executions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    broker VARCHAR(20) NOT NULL,
    account_name VARCHAR(50) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    trade_date DATE NOT NULL,
    ticker_code VARCHAR(100) NOT NULL,
    side ENUM('BUY', 'SELL') NOT NULL,
    quantity DECIMAL(15, 4) NOT NULL,
    unit_price DECIMAL(15, 4) NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'KRW',
    status ENUM('PENDING', 'APPROVED', 'REJECTED', 'DUPLICATE') NOT NULL DEFAULT 'PENDING',
    transaction_id INT DEFAULT NULL,
    notified_at DATETIME DEFAULT NULL,
    decided_at DATETIME DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_exec (broker, account_name, order_id, trade_date),
    KEY idx_exec_status (status, trade_date)
)
"""

# PENDING 행만 수량·단가를 갱신한다 (처리된 행은 건드리지 않아 무시한 체결이 되살아나지 않음)
_UPSERT = """
INSERT INTO broker_executions
    (broker, account_name, order_id, trade_date, ticker_code, side, quantity, unit_price, currency)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON DUPLICATE KEY UPDATE
    quantity = IF(status = 'PENDING', VALUES(quantity), quantity),
    unit_price = IF(status = 'PENDING', VALUES(unit_price), unit_price)
"""


def parse_since(value: str) -> date:
    """EXEC_SINCE 'YYYY-MM-DD' → date. 비었거나 형식이 틀리면 BrokerError (과거 체결이 쏟아지는 것 방지).

    >>> parse_since("2026-10-20")
    datetime.date(2026, 10, 20)
    """
    if not value:
        raise BrokerError("EXEC_SINCE(도입일, YYYY-MM-DD)가 비어 있습니다. 설정 후 다시 실행하세요")
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise BrokerError(f"EXEC_SINCE 형식 오류: {value!r} (YYYY-MM-DD)") from e


def filter_executions(executions: list[Execution], since: date) -> list[Execution]:
    """도입일 이후이고 수량·방향이 유효한 체결만.

    >>> e = {"broker": "toss", "order_id": "1", "account_name": "토스 일반", "trade_date": date(2026, 10, 20),
    ...      "symbol": "AAPL", "side": "BUY", "quantity": 1.0, "avg_price": 10.0, "currency": "USD"}
    >>> len(filter_executions([e, {**e, "order_id": "2", "trade_date": date(2026, 10, 1)}], date(2026, 10, 20)))
    1
    """
    return [
        e
        for e in executions
        if e["trade_date"] >= since
        and e["side"] in ("BUY", "SELL")
        and e["quantity"] > 0
        and e["avg_price"] > 0
    ]


def _key(e: Execution) -> tuple:
    return (e["broker"], e["account_name"], str(e["order_id"]), e["trade_date"])


def save_executions(executions: list[Execution], start: date) -> int:
    """UPSERT 하고 새로 들어간 건수를 반환. start 는 신규 건수 계산용 조회 하한(조회 구간 시작일)."""
    if not executions:
        return 0
    existing = {
        (r[0], r[1], r[2], r[3])
        for r in fetch_all(
            "SELECT broker, account_name, order_id, trade_date FROM broker_executions WHERE trade_date >= ?",
            (start,),
            strict=True,
        )
    }
    rows = [
        (
            e["broker"],
            e["account_name"],
            str(e["order_id"]),
            e["trade_date"],
            e["symbol"],
            e["side"],
            e["quantity"],
            e["avg_price"],
            e["currency"],
        )
        for e in executions
    ]
    if not execute_many(_UPSERT, rows):
        raise BrokerError("broker_executions 저장 실패 (DB 연결)")
    return sum(1 for e in executions if _key(e) not in existing)


def collect_executions() -> dict:
    """EXEC_BROKERS 의 최근 체결을 대기 테이블에 저장. 한 증권사가 실패해도 나머지는 계속한다."""
    since = parse_since(EXEC_SINCE)
    end = date.today()
    start = max(since, end - timedelta(days=EXEC_LOOKBACK_DAYS))
    try:
        execute(_DDL)  # 이미 있으면 무시 (봇이 시작 때 만든 경우가 보통)
    except Exception as e:
        raise BrokerError(
            f"broker_executions 테이블 준비 실패 (CREATE/SELECT/INSERT/UPDATE 권한 확인): {e}"
        ) from e
    summary = {"since": since, "fetched": 0, "new": 0, "failed": []}
    for name in EXEC_BROKERS:
        try:
            fetched = filter_executions(get_broker(name).executions(start, end), since)
            new = save_executions(fetched, start)
        except NotImplementedError as e:
            logger.warning(f"{name} 체결 조회 건너뜀: {e}")
            continue
        except BrokerError as e:
            logger.error(f"{name} 체결 수집 실패: {e}")
            summary["failed"].append(name)
            continue
        logger.info(f"{name} 체결 {len(fetched)}건 조회, 신규 {new}건 (구간 {start}~{end})")
        summary["fetched"] += len(fetched)
        summary["new"] += new
    return summary
