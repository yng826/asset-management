"""
core/fetcher/us_stock.py
- 해외 주식(미국 주식 직투) USD 종가 수집 모듈
"""

import re
from datetime import datetime, timedelta

import FinanceDataReader as fdr

from core.fetcher.kr_stock import upsert_daily_price
from database.repository import AssetRepository

_US_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}$")  # 미국 주식: 영문 대문자 1~5자리


def is_us_stock_ticker(ticker_code) -> bool:
    """미국 주식 여부 판별 (영문 대문자 1~5자리)

    예: AAPL, TSLA, QQQ, GOOGL, PFE
    """
    if not ticker_code:
        return False
    return bool(_US_TICKER_PATTERN.match(str(ticker_code).strip()))


def fetch_us_stock_close(ticker_code: str):
    """
    FinanceDataReader를 이용해 미국 주식의 가장 최근 영업일 USD 종가를 수집.

    Returns:
        {"ticker_code": str, "price_date": date, "close_price": float (USD)} 또는 None.
    """
    code = str(ticker_code).strip().upper()
    if not is_us_stock_ticker(code):
        print(f"⚠️ 미국 주식 티커가 아닙니다 (code={code}) - 건너뜀")
        return None

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        df = fdr.DataReader(code, start_date, end_date)
    except Exception as e:
        print(f"❌ FDR 호출 실패 [{code}]: {e}")
        return None

    if df is None or df.empty:
        print(f"⚠️ FDR 데이터 없음 [{code}]")
        return None

    latest = df.iloc[-1]
    price_date = df.index[-1].date()
    close_price = float(latest["Close"])

    return {
        "ticker_code": code,
        "price_date": price_date,
        "close_price": close_price,
    }


def collect_us_prices(verbose: bool = True) -> dict:
    """
    보유 종목 중 해외 주식 종가를 수집하여 DB에 저장.
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    summary = {
        "us_targets": 0,
        "fetched": 0,
        "saved": 0,
        "failed": 0,
        "results": [],
    }

    seen_codes: set = set()

    for h in holdings:
        code = h.get("ticker_code")
        name = h.get("ticker_name")

        if not is_us_stock_ticker(code):
            continue

        if code in seen_codes:
            continue
        seen_codes.add(code)

        summary["us_targets"] += 1
        if verbose:
            print(f"📈 해외 주식 수집 시도: {name} ({code}) [US]")

        data = fetch_us_stock_close(code)
        if not data:
            summary["failed"] += 1
            continue

        summary["fetched"] += 1
        saved = upsert_daily_price(
            ticker_code=data["ticker_code"],
            price_date=data["price_date"],
            close_price=data["close_price"],
        )
        result = {
            "ticker_code": data["ticker_code"],
            "price_date": data["price_date"],
            "close_price": data["close_price"],
            "saved": saved,
            "asset_class": "us",
        }
        summary["results"].append(result)
        if saved:
            summary["saved"] += 1
            if verbose:
                print(f"   ✅ 저장 완료: {code} {data['price_date']} ${data['close_price']:,.2f}")
        else:
            summary["failed"] += 1

    return summary
