"""
core/valuator/stock.py
- 주식/ETF 평가 로직 (국내 + 해외).

  1) 해외주식 (us_stock): ticker_code 가 영문 1~5자리 + price_map 매핑 + fx_rate 매핑
        - current_price_krw = usd_close × krw_per_usd
        - buy_amount 도 USD 매수액 × 환율 로 KRW 환산해 일관성 유지
        - price_source = "us_stock"

  2) 국내 주식/ETF (market): ticker_code 가 6자리 숫자/영숫자 + price_map 매핑
        - current_price = close_price
        - buy_amount = avg_price × quantity (KRW)
        - price_source = "market"

두 분기 모두 매칭 실패 시 None 반환 → calculator 가 fallback 으로 처리.
"""

import re

# 식별자 패턴 (price_fetcher 와 동일)
_US_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}$")
_KR_TICKER_PATTERN = re.compile(r"^[0-9A-Z]{6}$")


def is_us_stock_ticker(ticker_code) -> bool:
    """미국 주식 여부 판별 (영문 대문자 1~5자리).

    예: AAPL, TSLA, QQQ, GOOGL, PFE
    """
    if not ticker_code:
        return False
    return bool(_US_TICKER_PATTERN.match(str(ticker_code).strip().upper()))


def is_kr_stock_ticker(ticker_code) -> bool:
    """국내 주식/ETF 여부 판별 (6자리 영숫자 혼용).

    예: 005930, 278530, 0181L0 (ETF 단축코드)
    """
    if not ticker_code:
        return False
    return bool(_KR_TICKER_PATTERN.match(str(ticker_code).strip()))


def _valuation_us_stock(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
    """해외주식 (KRW 환산)."""
    ticker_code = asset.get("ticker_code")
    usd_info = price_map.get(str(ticker_code))
    if not usd_info:
        return None

    usd_close = float(usd_info["close_price"])  # USD per share
    usd_date = usd_info["price_date"]

    if not fx_rate:
        return None  # 환율 없으면 매칭 실패 → fallback
    krw_per_usd = float(fx_rate["rate"])

    avg_price = float(asset.get("avg_price", 0.0) or 0.0)  # USD per share
    quantity = float(asset.get("quantity", 0.0) or 0.0)

    # 매수금액도 KRW 환산해 일관성 유지
    buy_amount_usd = avg_price * quantity
    buy_amount_krw = buy_amount_usd * krw_per_usd

    current_price_krw = usd_close * krw_per_usd
    valuation_amount = current_price_krw * quantity
    profit = valuation_amount - buy_amount_krw

    from core.calculator import _safe_pnl_rate  # lazy import

    pnl_rate = _safe_pnl_rate(profit, buy_amount_krw)

    return {
        **asset,
        "current_price": current_price_krw,
        "price_date": usd_date,
        "price_source": "us_stock",
        "buy_amount": buy_amount_krw,  # KRW 환산값으로 통일
        "valuation_amount": valuation_amount,
        "profit": profit,
        "pnl_rate": pnl_rate,
        "_us": {
            "usd_close": usd_close,
            "krw_per_usd": krw_per_usd,
            "fx_date": fx_rate["price_date"],
            "buy_amount_usd": buy_amount_usd,
        },
    }


def _valuation_kr_stock(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
    """국내 주식/ETF."""
    ticker_code = asset.get("ticker_code")
    info = price_map.get(str(ticker_code))
    if not info:
        return None

    current_price = float(info["close_price"])
    price_date = info["price_date"]
    avg_price = float(asset.get("avg_price", 0.0) or 0.0)
    quantity = float(asset.get("quantity", 0.0) or 0.0)

    buy_amount = avg_price * quantity
    valuation_amount = current_price * quantity
    profit = valuation_amount - buy_amount

    from core.calculator import _safe_pnl_rate  # lazy import

    pnl_rate = _safe_pnl_rate(profit, buy_amount)

    return {
        **asset,
        "current_price": current_price,
        "price_date": price_date,
        "price_source": "market",
        "buy_amount": buy_amount,
        "valuation_amount": valuation_amount,
        "profit": profit,
        "pnl_rate": pnl_rate,
    }


def valuation(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
    """valuator 공통 인터페이스 구현 — 주식(국내+해외) 평가.

    우선순위:
      1) 해외주식 (영문 1~5자리)
      2) 국내 주식/ETF (6자리 영숫자)
    둘 다 매칭 실패 시 None 반환 → calculator 가 fallback 으로 처리.
    """
    ticker_code = asset.get("ticker_code")
    if not ticker_code:
        return None

    # 해외주식 우선 (덜 일반적이나, 환율 + USD 시세 둘 다 필요)
    if is_us_stock_ticker(ticker_code):
        result = _valuation_us_stock(asset, price_map, fx_rate)
        if result is not None:
            return result

    # 국내 주식/ETF
    if is_kr_stock_ticker(ticker_code):
        result = _valuation_kr_stock(asset, price_map, fx_rate)
        if result is not None:
            return result

    return None
