"""
core/fetcher/broker_prices.py
- 증권사 Open API 종가 수집 (보유 국내 주식/ETF·미국 주식), 종목별 FDR 폴백
- 섀도 테이블(PRICE_TABLE=daily_prices_broker)에 쌓아 FDR 시세(daily_prices)와 비교하는 용도
"""

import logging

from config.settings import PRICE_BROKER
from core.broker import BrokerError, get_broker
from core.fetcher.kr_stock import (
    KR_REFRESH_DAYS,
    fetch_kr_stock_closes,
    is_kr_stock_ticker,
    upsert_daily_price,
)
from core.fetcher.us_stock import fetch_us_stock_close, is_us_stock_ticker
from database.repository import AssetRepository

logger = logging.getLogger(__name__)


def _fdr_closes(code: str, is_kr: bool) -> list[dict]:
    """FDR 폴백: 국내는 최근 KR_REFRESH_DAYS 일, 미국은 기존 수집과 같이 최신 1일."""
    if is_kr:
        return fetch_kr_stock_closes(code)
    data = fetch_us_stock_close(code)
    return [{"price_date": data["price_date"], "close_price": data["close_price"]}] if data else []


def collect_broker_prices() -> dict:
    """
    보유 종목 중 국내·미국 주식 종가를 증권사 API 로 수집해 PRICE_TABLE 에 UPSERT.
    - 증권사 실패·빈 응답이면 그 종목만 FDR 로 폴백 (종목별 출처를 로그에 기록)
    - 최근 KR_REFRESH_DAYS 영업일을 덮어써 미확정 종가를 보정하고 놓친 날을 복구
    """
    holdings = AssetRepository().get_current_holdings()
    broker = get_broker(PRICE_BROKER)
    summary = {"targets": 0, "broker": 0, "fallback": 0, "failed": [], "fallback_codes": []}

    seen: set = set()
    for h in holdings:
        code = str(h.get("ticker_code") or "").strip()
        is_kr = is_kr_stock_ticker(code)
        if code in seen or not (is_kr or is_us_stock_ticker(code)):
            continue
        seen.add(code)
        summary["targets"] += 1

        source = broker.name
        try:
            closes = broker.daily_closes(code, KR_REFRESH_DAYS)
        except BrokerError as e:
            logger.warning(f"{broker.name} 시세 실패 [{code}]: {e}")
            closes = []
        if not closes:
            source = "fdr(폴백)"
            closes = _fdr_closes(code, is_kr)

        if not closes:
            logger.error(f"시세 없음 [{code}]: {broker.name}·FDR 모두 실패")
            summary["failed"].append(code)
            continue

        results = [upsert_daily_price(code, c["price_date"], c["close_price"]) for c in closes]
        saved = all(results)
        logger.info(
            f"[{code}] 출처={source} {closes[-1]['price_date']} {closes[-1]['close_price']:g} 저장={saved}"
        )
        if not saved:
            summary["failed"].append(code)
        elif source == broker.name:
            summary["broker"] += 1
        else:
            summary["fallback"] += 1
            summary["fallback_codes"].append(code)
    return summary
