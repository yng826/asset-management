"""토스증권 Open API 클라이언트 (조회 전용, 스펙 v1.2.24)

시세(캔들)·계좌·체결 주문·보유 종목만 사용한다. 주문 계열은 구현하지 않는다.
응답은 {"result": ...} envelope, 숫자는 문자열(decimal)로 온다.
"""

import logging
import time
from datetime import date, datetime, timedelta, timezone

import requests

from config.settings import TOSS_CLIENT_ID, TOSS_CLIENT_SECRET

from . import token_store
from .base import BrokerError, Close, Execution

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.tossinvest.com"
TIMEOUT = 10
MAX_RETRIES_429 = 2
KST = timezone(timedelta(hours=9))
ACCOUNT_NAME = "토스 일반"  # config/constants.py 의 계좌명 (D6)


def _parse_closes(payload: dict) -> list[Close]:
    """GET /candles 응답(최신순) → 오래된 순 [{price_date, close_price}]."""
    rows = [
        {"price_date": datetime.fromisoformat(c["timestamp"]).date(), "close_price": float(c["closePrice"])}
        for c in payload["result"]["candles"]
    ]
    rows.sort(key=lambda r: r["price_date"])
    return rows


def _parse_executions(orders: list[dict], account_name: str = ACCOUNT_NAME) -> list[Execution]:
    """주문 목록 → 체결 수량이 있는 주문만 공통 형태로."""
    result: list[Execution] = []
    for o in orders:
        ex = o.get("execution") or {}
        qty = float(ex.get("filledQuantity") or 0)
        if qty <= 0 or not ex.get("averageFilledPrice") or not ex.get("filledAt"):
            continue
        result.append(
            {
                "broker": "toss",
                "order_id": o["orderId"],
                "account_name": account_name,
                "trade_date": datetime.fromisoformat(ex["filledAt"]).astimezone(KST).date(),
                "symbol": o["symbol"],
                "side": o["side"],
                "quantity": qty,
                "avg_price": float(ex["averageFilledPrice"]),
                "currency": o["currency"],
            }
        )
    return result


class TossClient:
    name = "toss"

    def __init__(self) -> None:
        if not (TOSS_CLIENT_ID and TOSS_CLIENT_SECRET):
            raise BrokerError("토스 키 미설정: TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 환경변수 필요")
        self._http = requests.Session()
        self._account_seq: int | None = None

    # --- 인증 / 요청 ---------------------------------------------------
    def _issue_token(self) -> tuple[str, int]:
        r = self._http.post(
            f"{BASE_URL}/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": TOSS_CLIENT_ID,
                "client_secret": TOSS_CLIENT_SECRET,
            },
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise BrokerError(f"토스 토큰 발급 실패 HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        return body["access_token"], int(body["expires_in"])

    def _get(self, path: str, params: dict | None = None, account: bool = False) -> dict:
        reissued = False
        retries = 0
        while True:
            headers = {"Authorization": f"Bearer {token_store.get_token(self.name, self._issue_token)}"}
            if account:
                headers["X-Tossinvest-Account"] = str(self._get_account_seq())
            try:
                r = self._http.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=TIMEOUT)
            except requests.RequestException as e:
                raise BrokerError(f"토스 요청 실패 {path}: {e}") from e
            remaining = r.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                logger.debug(f"토스 {path} 한도 잔여 {remaining}/{r.headers.get('X-RateLimit-Limit')}")
            if r.status_code == 401 and not reissued:
                token_store.invalidate(self.name)
                reissued = True
                continue
            if r.status_code == 429 and retries < MAX_RETRIES_429:
                retries += 1
                wait = float(r.headers.get("Retry-After") or 1)
                logger.warning(f"토스 429 한도 초과, {wait}초 후 재시도 ({retries}/{MAX_RETRIES_429})")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                raise BrokerError(f"토스 {path} HTTP {r.status_code}: {r.text[:200]}")
            return r.json()

    def _get_account_seq(self) -> int:
        if self._account_seq is None:
            accounts = self._get("/api/v1/accounts")["result"]
            if len(accounts) != 1:
                raise BrokerError(f"토스 계좌가 {len(accounts)}개입니다 (1개만 지원, 계좌명 {ACCOUNT_NAME})")
            self._account_seq = int(accounts[0]["accountSeq"])
        return self._account_seq

    # --- 조회 ----------------------------------------------------------
    def daily_closes(self, symbol: str, days: int) -> list[Close]:
        payload = self._get(
            "/api/v1/candles",
            {"symbol": symbol, "interval": "1d", "count": min(max(days, 1), 200), "adjusted": "false"},
        )
        return _parse_closes(payload)

    def executions(self, start: date, end: date) -> list[Execution]:
        orders: list[dict] = []
        cursor = None
        while True:
            params = {"status": "CLOSED", "from": start.isoformat(), "to": end.isoformat(), "limit": 100}
            if cursor:
                params["cursor"] = cursor
            result = self._get("/api/v1/orders", params, account=True)["result"]
            orders.extend(result["orders"])
            if not result.get("hasNext"):
                break
            cursor = result["nextCursor"]
        return _parse_executions(orders)

    def holdings(self) -> list[dict]:
        items = self._get("/api/v1/holdings", account=True)["result"]["items"]
        return [
            {
                "symbol": i["symbol"],
                "name": i["name"],
                "quantity": float(i["quantity"]),
                "currency": i["currency"],
            }
            for i in items
        ]
