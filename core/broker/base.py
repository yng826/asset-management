"""증권사 공통 인터페이스 (조회 전용 — 주문·정정·취소 기능은 두지 않는다)"""

from datetime import date
from typing import Protocol, TypedDict


class BrokerError(RuntimeError):
    """증권사 API 호출 실패 (미설정·인증·한도·응답 형식 오류 공통)."""


class Close(TypedDict):
    price_date: date
    close_price: float


class Execution(TypedDict):
    broker: str
    order_id: str
    account_name: str
    trade_date: date
    symbol: str
    side: str  # "BUY" | "SELL"
    quantity: float
    avg_price: float
    currency: str


class BrokerClient(Protocol):
    name: str

    def daily_closes(self, symbol: str, days: int) -> list[Close]:
        """최근 days 거래일 종가, 오래된 순."""

    def executions(self, start: date, end: date) -> list[Execution]:
        """start~end(포함) 체결된 주문."""

    def holdings(self) -> list[dict]:
        """보유 종목 [{symbol, name, quantity, currency}]."""
