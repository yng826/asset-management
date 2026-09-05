"""
core/valuator/fund.py
- 펀드 표준코드 (K55..., KR5...) 평가 로직.
- 평가식: 평가액 = 보유수량(좌수) × (NAV / 1000)

데이터 소스: NAV 는 price_map[ticker_code] (외부 collector 가 daily_prices 에 UPSERT)
"""

# 펀드 표준코드 식별 (한국 펀드 표준 코드 패턴)
_FUND_TICKER_PREFIXES = ("K55", "KR5")


def is_fund_ticker(ticker_code) -> bool:
    """IRP/연금 펀드 표준 코드 여부 판별.

    예: K55234BX0537, KR5301AW7849
    - ticker_code가 'K55' 또는 'KR5' 로 시작 + 길이 10+
    """
    if not ticker_code:
        return False
    code = str(ticker_code).strip().upper()
    return code.startswith(_FUND_TICKER_PREFIXES) and len(code) >= 10


def valuation(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
    """valuator 공통 인터페이스 구현 — 펀드 평가.

    매칭 조건: ticker_code in K55/KR5 + price_map 매칭 성공.
    """
    ticker_code = asset.get("ticker_code")
    if not is_fund_ticker(ticker_code):
        return None

    fund_info = price_map.get(str(ticker_code))
    if not fund_info:
        return None

    nav = float(fund_info["close_price"])  # 1000좌당 기준가
    nav_date = fund_info["price_date"]
    avg_price = float(asset.get("avg_price", 0.0) or 0.0)
    quantity = float(asset.get("quantity", 0.0) or 0.0)
    buy_amount = avg_price * quantity

    valuation_amount = quantity * (nav / 1000.0)
    profit = valuation_amount - buy_amount

    from core.calculator import _safe_pnl_rate  # lazy import

    pnl_rate = _safe_pnl_rate(profit, buy_amount)

    return {
        **asset,
        "current_price": nav,  # 1000좌당 NAV 표시
        "price_date": nav_date,
        "price_source": "fund_nav",
        "buy_amount": buy_amount,
        "valuation_amount": valuation_amount,
        "profit": profit,
        "pnl_rate": pnl_rate,
        "_fund": {
            "nav_per_1000": nav,
            "quantity_units": quantity,
            "nav_date": nav_date,
        },
    }
