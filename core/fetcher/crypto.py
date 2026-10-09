"""
core/fetcher/crypto.py
- 가상자산 시세 수집 모듈 (Upbit API)

일봉 종가 기준 (업비트 표기와 동일):
  - price_date D 의 종가 = D 09:00 ~ D+1 09:00 KST 일봉의 종가 → D+1 09:00 에 확정
  - daily_prices 에는 확정된 일봉 종가만 저장 (장중 현재가는 저장하지 않음, 실시간은 /live)
"""

from datetime import datetime, timedelta

import pyupbit
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
    - close_price: 현재가 (trade_price)
    - prev_closing_price: 전일 종가 (09:00 KST 확정, /live 등락 기준)
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
                results[market] = {
                    "price_date": datetime.now().date(),
                    "close_price": float(trade_price),
                    # 업비트 일봉 기준 전일 종가 (매일 09:00 KST 확정)
                    "prev_closing_price": float(item.get("prev_closing_price") or 0.0),
                }
        return results
    except Exception as e:
        print(f"❌ Upbit API 수집 실패: {e}")
        return {}


def fetch_crypto_daily_closes(ticker_code: str, count: int = 3) -> list[dict]:
    """업비트 일봉 중 확정된(D+1 09:00 KST 경과) 종가 목록 반환 [{price_date, close_price}, ...].

    - count: 최근 일봉 조회 개수 (진행 중인 당일 일봉 포함, 확정분만 반환 → 누락일 자동 보충)
    """
    try:
        df = pyupbit.get_ohlcv(ticker_code, interval="day", count=count)
    except Exception as e:
        print(f"❌ Upbit 일봉 조회 실패 [{ticker_code}]: {e}")
        return []
    if df is None or df.empty:
        return []

    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    now = datetime.now()
    return [
        {"price_date": idx.date(), "close_price": float(row["close"])}
        for idx, row in df.iterrows()
        if idx + timedelta(days=1) <= now
    ]


def collect_crypto_prices(verbose: bool = True) -> dict:
    """
    보유 자산 중 가상자산의 확정 일봉 종가를 일괄 수집하여 DB에 저장.
    - 최근 3개 일봉 중 확정분만 UPSERT (09:00 KST 이후 실행 시 전일 종가 반영)
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

    for code in crypto_tickers:
        closes = fetch_crypto_daily_closes(code)
        if not closes:
            summary["failed"] += 1
            continue

        summary["fetched"] += 1
        saved_all = True
        for data in closes:
            saved_all = (
                upsert_daily_price(
                    ticker_code=code,
                    price_date=data["price_date"],
                    close_price=data["close_price"],
                )
                and saved_all
            )
        latest = closes[-1]
        result = {
            "ticker_code": code,
            "price_date": latest["price_date"],
            "close_price": latest["close_price"],
            "saved": saved_all,
            "asset_class": "crypto",
        }
        summary["results"].append(result)
        if saved_all:
            summary["saved"] += 1
            if verbose:
                print(f"   ✅ 저장 완료: {code} {latest['price_date']} ₩{latest['close_price']:,.2f}")
        else:
            summary["failed"] += 1

    return summary
