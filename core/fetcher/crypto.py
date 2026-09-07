"""
core/fetcher/crypto.py
- 가상자산 시세 수집 모듈 (Upbit API)
"""

from datetime import datetime

import requests

from core.fetcher.kr_stock import upsert_daily_price
from database.repository import AssetRepository


def is_crypto_ticker(ticker_code) -> bool:
    """가상자산 여부 판별 (KRW-XXX)"""
    if not ticker_code:
        return False
    return str(ticker_code).strip().startswith("KRW-")


def fetch_crypto_price(ticker_codes: list[str]) -> dict:
    """
    Upbit Public API를 사용하여 가상자산 현재가 수집 (다중 마켓 지원)
    """
    if not ticker_codes:
        return {}

    url = f"https://api.upbit.com/v1/ticker?markets={','.join(ticker_codes)}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        results = {}
        for item in data:
            market = item.get("market")
            trade_price = item.get("trade_price")
            if market and trade_price:
                results[market] = {"price_date": datetime.now().date(), "close_price": float(trade_price)}
        return results
    except Exception as e:
        print(f"❌ Upbit API 수집 실패: {e}")
        return {}


def collect_crypto_prices(verbose: bool = True) -> dict:
    """
    보유 자산 중 가상자산 시세를 일괄 수집하여 DB에 저장.
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    crypto_tickers = []
    seen_codes = set()
    for h in holdings:
        code = h.get("ticker_code")
        if is_crypto_ticker(code) and code not in seen_codes:
            seen_codes.add(code)
            crypto_tickers.append(code)

    summary = {
        "crypto_targets": len(crypto_tickers),
        "fetched": 0,
        "saved": 0,
        "failed": 0,
        "results": [],
    }

    if not crypto_tickers:
        return summary

    if verbose:
        print(f"🪙 가상자산 일괄 수집 시도: {crypto_tickers}")

    prices = fetch_crypto_price(crypto_tickers)
    for code in crypto_tickers:
        data = prices.get(code)
        if not data:
            summary["failed"] += 1
            continue

        summary["fetched"] += 1
        saved = upsert_daily_price(
            ticker_code=code,
            price_date=data["price_date"],
            close_price=data["close_price"],
        )
        result = {
            "ticker_code": code,
            "price_date": data["price_date"],
            "close_price": data["close_price"],
            "saved": saved,
            "asset_class": "crypto",
        }
        summary["results"].append(result)
        if saved:
            summary["saved"] += 1
            if verbose:
                print(f"   ✅ 저장 완료: {code} {data['price_date']} ₩{data['close_price']:,.2f}")
        else:
            summary["failed"] += 1

    return summary
