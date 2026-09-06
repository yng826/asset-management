"""
core/valuator/crypto.py
- 가상자산(코인) 평가 로직.
"""


def is_crypto_ticker(ticker_code: str) -> bool:
    """가상자산 여부 판별 (KRW-XXX)"""
    if not ticker_code:
        return False
    return str(ticker_code).strip().startswith("KRW-")


def valuation(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
    """가상자산 평가 로직"""
    code = asset.get("ticker_code")
    if not is_crypto_ticker(code):
        return None

    # price_map에서 시세 조회
    price_info = price_map.get(code)
    if not price_info:
        return None

    current_price = price_info.get("close_price")
    quantity = asset.get("quantity", 0)
    avg_price = asset.get("avg_price", 0)

    valuation_amount = quantity * current_price
    buy_amount = quantity * avg_price
    profit = valuation_amount - buy_amount

    return {
        **asset,
        "ticker_code": code,
        "current_price": current_price,
        "valuation_amount": valuation_amount,
        "buy_amount": buy_amount,
        "profit": profit,
        "pnl_rate": (profit / buy_amount * 100) if buy_amount > 0 else 0,
        "price_source": "crypto",
    }
