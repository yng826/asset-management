"""증권사 Open API 공통 틀 (조회 전용). 사용: get_broker("toss").daily_closes("005930", days=5)"""

from .base import BrokerClient, BrokerError


def get_broker(name: str) -> BrokerClient:
    if name == "toss":
        from .toss import TossClient

        return TossClient()
    raise BrokerError(f"지원하지 않는 증권사: {name} (toss)")


__all__ = ["BrokerClient", "BrokerError", "get_broker"]
